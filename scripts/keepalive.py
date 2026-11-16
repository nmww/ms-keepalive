#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔搭创空间保活脚本（GitHub Actions / 本地通用）
读取同仓库 keepalive.json，并行模拟浏览器 GET 所有链接：
  200        = 在线保温成功
  404/502/503= 已发出冷启动唤醒信号（1-2分钟后自动启动）
  401/403    = 已下线/受限，需手动点平台【发布】（只提醒，不操作）
只做 GET，绝不调用发布/部署接口。结果写入 keepalive.log 并输出摘要。
"""
import json, os, urllib.request, urllib.error, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(BASE, "keepalive.json")
LOG = os.path.join(BASE, "keepalive.log")
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
        return "ERR:" + str(e)[:40]


def main():
    conf = json.load(open(CONF, encoding="utf-8"))
    # 兼容新旧两种结构：projects / include
    items = conf.get("projects") or conf.get("include") or []
    lines = []
    def log(msg):
        line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        lines.append(line)

    log("=== keepalive start: %d projects ===" % len(items))
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(ping, p["url"]): p for p in items}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                results[p["name"]] = fut.result()
            except Exception as e:
                results[p["name"]] = "ERR:" + str(e)[:40]

    ok = wake = bad = 0
    for p in items:
        name, code = p["name"], results.get(p["name"], "?")
        if code == 200:
            log("%-24s %-6s 在线保温" % (name, code)); ok += 1
        elif code in (404, 502, 503):
            log("%-24s %-6s 已发出唤醒信号(冷启动1-2分钟)" % (name, code)); wake += 1
        elif code in (401, 403):
            log("%-24s %-6s 已下线/受限，需手动点【发布】" % (name, code)); bad += 1
        else:
            log("%-24s %-6s 异常待查" % (name, code)); bad += 1

    log("=== done: 在线保温=%d 唤醒=%d 需人工=%d ===" % (ok, wake, bad))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    # 有项目需人工处理时返回非0，方便在 Actions 里醒目提示（但不影响其他项目保温）
    if bad:
        print("::warning::%d 个项目需人工检查，详见日志" % bad, flush=True)


if __name__ == "__main__":
    main()
