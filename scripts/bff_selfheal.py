#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蓝色大肥鱼（modeai/blue-fat-fish）云端自愈，供 GitHub Actions 定时调用。

与本地 keepalive.py 的区别：
  - 令牌从环境变量 MODELSCOPE_TOKEN 读（GitHub Secrets 注入），不读本地文件、不进代码。
  - GET 裸域名保活（带 UA）；非 200 判定为僵尸 → POST /deploy 重建 → 轮询 run 日志
    出现 'listening' 后复验，结果写回 bff-heartbeat.json 作为心跳留痕。

魔搭僵尸特征：平台 status=Running 但容器已死，页面 504，GET 不通。
GET 唤醒只对正常休眠有效，死容器必须 POST /deploy 重建（平台不会自动重建）。

仅依赖标准库，ubuntu-latest 自带 python3 即可跑，不装任何包。
"""
import json
import os
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


def visit():
    """带 UA 访问一次；dsh 首访 303 发 cookie 后跳回 ./，跟随重定向即可。"""
    req = urllib.request.Request(MS_URL, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read(200000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception as e:
        return 0, repr(e)


def wait_listening(rounds=30, wait=20):
    """轮询 run 日志直到 dsh listening，最多 10 分钟。"""
    for i in range(rounds):
        time.sleep(wait)
        _, res = api('GET', '/studios/%s/logs/run' % STUDIO)
        logs = (res.get('data') or {}).get('logs') or []
        if 'listening' in ''.join(logs):
            log('dsh listening 确认（第 %d 轮）' % (i + 1))
            return True
    return False


def main():
    if not TOKEN:
        log('FATAL: MODELSCOPE_TOKEN 未注入')
        raise SystemExit(1)
    log('=== 大肥鱼云端自愈开始 ===')
    code, body = visit()
    status, vc = studio_info()
    log('首访 http=%s status=%s view_count=%s' % (code, status, vc))

    result, detail = 'ok', ''
    if code != 200:
        result = 'rebuilding'
        detail = 'http=%s，触发 deploy 重建' % code
        log(detail)
        st, _ = api('POST', '/studios/%s/deploy' % STUDIO)
        log('deploy 触发 HTTP %s' % st)
        if wait_listening():
            code, body = visit()
            status, vc = studio_info()
            result = 'rebuilt'
            detail = '重建成功，http=%s' % code
        else:
            result = 'failed'
            detail = 'deploy 后 10 分钟未 listening'
    else:
        ok = 'DeepSeek Harness' in body
        detail = '保活正常，页面标题校验%s' % ('通过' if ok else '未通过')

    log('结果：%s | %s' % (result, detail))
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    hb = {'event_type': 'actions', 'http_code': str(code),
          'view_count': vc if isinstance(vc, int) else None,
          'studio_status': status, 'result': result,
          'detail': detail[:500], 'checked_at': now}
    with open(HEARTBEAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(hb, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('HEARTBEAT_JSON=' + json.dumps(hb, ensure_ascii=False))


if __name__ == '__main__':
    main()
