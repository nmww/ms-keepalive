#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔搭创空间保活脚本（GitHub Actions / 本地通用）
读取同仓库 keepalive.json，并行模拟浏览器 GET 所有链接：
  200        = 在线保温成功
  404/502/503= 已发出冷启动唤醒信号（1-2分钟后自动启动）
  401/403    = 已下线/受限，需手动点平台【发布】（只提醒，不操作）
只做 GET，绝不调用发布/部署接口。
结果写入 keepalive.log + status.json（结构化心跳，供在线管理台读取）。

⛔ 为什么要有 status.json：GitHub Actions 页面人工看日志不可持续，
   在线管理台只能读仓库里的静态文件（raw.githubusercontent.com 免鉴权），
   所以每次保活必须把结构化结果 commit 回仓库，管理台才有心跳数据。
   不要改成写数据库/调外部 API——会引入 token 暴露和依赖，破坏纯静态架构。
"""
import json, os, sys, time, urllib.request, urllib.error, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(BASE, "keepalive.json")
LOG = os.path.join(BASE, "keepalive.log")
STATUS = os.path.join(BASE, "status.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def ping(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return "ERR:" + str(e)[:60]


def ping_with_wake(url, timeout=40):
    """先 GET 探活；若 404（休眠唤醒中）等待后复测，避免把冷启动中间态误记成常态。
    ⛔ 不要去掉复测：魔搭冷启动需 1-2 分钟，单次 GET 抓到的 404 是瞬时值，
       用户随后打开变 200 后管理台仍显示"唤醒中"就是这个问题。复测让心跳贴近真实。
    """
    code = ping(url, timeout)
    if code in (404, 502, 503):
        # 给冷启动留时间，复测两轮（共约 70 秒），取最后一次有效状态
        for wait in (30, 40):
            time.sleep(wait)
            code2 = ping(url, timeout)
            if code2 not in (404, 502, 503):
                return code2
            code = code2
    return code


def classify(code):
    # 2xx/3xx 都算在线：3xx 是正常重定向（如大肥鱼 dsh 首访 303 发 auth cookie 后跳回 ./）
    if (isinstance(code, int) and 200 <= code < 400) or code == "200":
        return "online"
    if code in (404, 502, 503):
        return "waking"
    return "bad"


def main():
    conf = json.load(open(CONF, encoding="utf-8"))
    items = conf.get("projects") or conf.get("include") or []
    lines = []

    def log(msg):
        line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        lines.append(line)

    now = datetime.datetime.now(datetime.timezone.utc)
    log("=== keepalive start: %d projects ===" % len(items))

    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(ping_with_wake, p["url"]): p for p in items}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                results[p["name"]] = fut.result()
            except Exception as e:
                results[p["name"]] = "ERR:" + str(e)[:60]

    details = []
    ok = wake = bad = 0
    for p in items:
        name, code = p["name"], results.get(p["name"], "?")
        state = classify(code)
        if state == "online":
            log("%-24s %-6s 在线保温" % (name, code)); ok += 1
        elif state == "waking":
            log("%-24s %-6s 已发出唤醒信号(冷启动1-2分钟)" % (name, code)); wake += 1
        else:
            log("%-24s %-6s 异常待查/需人工" % (name, code)); bad += 1
        details.append({
            "name": name,
            "url": p["url"],
            "code": str(code),
            "status": state,
        })

    log("=== done: 在线保温=%d 唤醒=%d 需人工=%d ===" % (ok, wake, bad))

    # 结构化心跳：管理台读这个文件
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    status = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at_local": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_id": run_id,
        "source": "github-actions" if run_id else "local",
        "total": len(items),
        "ok": ok,
        "waking": wake,
        "bad": bad,
        "results": details,
    }
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if bad:
        print("::warning::%d 个项目需人工检查，详见日志" % bad, flush=True)
    print("HEARTBEAT_STATUS=" + json.dumps(
        {"total": len(items), "ok": ok, "waking": wake, "bad": bad},
        ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
