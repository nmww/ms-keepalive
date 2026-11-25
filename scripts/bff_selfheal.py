#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蓝色大肥鱼（modeai/blue-fat-fish）云端自愈，供 GitHub Actions 定时调用。

与本地 keepalive.py 的区别：
  - 令牌从环境变量 MODELSCOPE_TOKEN 读（GitHub Secrets 注入），不读本地文件、不进代码。
  - 判僵尸不再用「裸域名 GET 非 200」——空间设私有后裸域名网关必返 403，
    会把每次探测都误判成僵尸、每小时强制重建（违反「稳定不靠重建」）。
    改用 OpenAPI run log 的密集 health check failed 判据（见 zombie_check）。
  - 确证僵尸才 POST /deploy 重建 → 轮询 run 日志出现 'listening' 后复验，
    结果写回 bff-heartbeat.json 作为心跳留痕。

判据（2026-11-25 实测四类用例全通过）：
  - 健康实例：run log 末尾全是 FC Invoke Start/End，零 health check failed → 不重建。
  - 僵尸实例：末尾密集 `Function instance health check failed on port 7860`（≥3 次）→ 重建。
  - 历史失败 + 近期健康：失败行已被 FC 日志挤出末尾 40 行 → 不误杀。
  - 空日志 / API 异常：保守不重建（宁可漏，不可误杀健康实例）。

仅依赖标准库，ubuntu-latest 自带 python3 即可跑，不装任何包。
"""
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

STUDIO = 'modeai/blue-fat-fish'
MS_URL = 'https://modeai-blue-fat-fish.ms.show/'
API = 'https://modelscope.cn/openapi/v1'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
TOKEN = os.environ.get('MODELSCOPE_TOKEN', '')
HEARTBEAT_FILE = 'bff-heartbeat.json'
FAIL_THRESHOLD = 3          # 最近日志中 health check failed >= 3 次判僵尸
RECENT_LINES = 40           # 只看最近 N 行，避免历史失败误判
STALE_MINUTES = 30          # FC Invoke 时间戳停滞超过此值也算异常（辅助判据）


def log(msg):
    print('[%s] %s' % (datetime.now().strftime('%F %T'), msg), flush=True)


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN,
                 'Content-Type': 'application/json', 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode('utf-8', 'replace'))
    except Exception as e:
        return -1, {'error': repr(e)}


def studio_info():
    _, res = api('GET', '/studios/' + STUDIO)
    d = res.get('data', {}) or {}
    return d.get('status', '?'), d.get('view_count')


def run_logs():
    """取运行日志（API 只回最近 100 行），返回非空行列表。"""
    _, res = api('GET', '/studios/%s/logs/run' % STUDIO)
    logs = (res.get('data') or {}).get('logs') or []
    if isinstance(logs, str):
        logs = [logs]
    return [l for l in logs if l and l.strip()]


def zombie_check():
    """判僵尸：只看最近 RECENT_LINES 行的密集 health check failed。
    返回 (is_zombie, reason, latest_fc_minutes_ago)。
    ⛔ 私有空间裸域名必 403，绝不能用 http 码判僵尸。"""
    logs = run_logs()
    recent = logs[-RECENT_LINES:]
    tail = '\n'.join(recent)
    fails = tail.count('health check failed')
    if fails >= FAIL_THRESHOLD:
        return True, '最近 %d 行有 %d 次 health check failed' % (len(recent), fails), None
    return False, '未见密集失败 (fails=%d, 最近 %d 行)' % (fails, len(recent)), None


def visit():
    """带 UA + cookie jar 访问一次裸域名——仅作保活，其结果不再用于判僵尸。
    dsh 首访 303 发 auth cookie 后跳回 ./，必须带 jar 跟随。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(MS_URL, headers={'User-Agent': UA})
    try:
        with opener.open(req, timeout=25) as r:
            return r.status, r.read(200000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception as e:
        return 0, repr(e)


def wait_listening(rounds=30, wait=20):
    """轮询 run 日志直到 dsh listening，最多 10 分钟。"""
    for i in range(rounds):
        time.sleep(wait)
        logs = run_logs()
        if 'listening' in '\n'.join(logs[-12:]):
            log('dsh listening 确认（第 %d 轮）' % (i + 1))
            return True
    return False


def main():
    if not TOKEN:
        log('FATAL: MODELSCOPE_TOKEN 未注入')
        raise SystemExit(1)
    log('=== 大肥鱼云端自愈开始 ===')
    code, body = visit()                      # 仅保活，不用于判定
    status, vc = studio_info()
    is_zombie, reason, _ = zombie_check()
    log('首访 http=%s(仅保活) status=%s view_count=%s | 僵尸判定=%s (%s)'
        % (code, status, vc, is_zombie, reason))

    result, detail = 'ok', ''
    if is_zombie:
        result = 'rebuilding'
        detail = reason + '，触发 deploy 重建'
        log(detail)
        st, _ = api('POST', '/studios/%s/deploy' % STUDIO)
        log('deploy 触发 HTTP %s' % st)
        if wait_listening():
            _, after_reason, _ = zombie_check()
            result = 'rebuilt'
            detail = '重建成功（%s）' % after_reason
        else:
            result = 'failed'
            detail = 'deploy 后 10 分钟未 listening'
    else:
        detail = '健康（%s）；裸域名 http=%s 仅供参考（私有空间必 403）' % (reason, code)

    log('结果：%s | %s' % (result, detail))
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    hb = {'event_type': 'actions', 'http_code': str(code),
          'view_count': vc if isinstance(vc, int) else None,
          'studio_status': status, 'result': result,
          'detail': detail[:500], 'zombie_check': reason, 'checked_at': now}
    with open(HEARTBEAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(hb, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('HEARTBEAT_JSON=' + json.dumps(hb, ensure_ascii=False))


if __name__ == '__main__':
    main()
