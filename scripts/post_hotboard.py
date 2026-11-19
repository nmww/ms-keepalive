#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热榜台每日帖 · 云端发帖器（GitHub Actions）

背景：魔搭新规禁止「免费创空间访问免费创空间」，hotboard 容器内已无法直连
discuz 发帖（HTTP 403）。本脚本从云端（GitHub Actions runner，非创空间）读取
hotboard 线上榜单，经 Discuz agentops.php 直连数据库发帖，绕开该限制。

流程：
  1. GET https://modeai-hotboard.ms.show/api/daily-rank?day=北京时间今天 → payload(subject,bbcode)
  2. 幂等：当天主题+首帖齐全 → 跳过；主题在首帖缺 → 补帖；都没有 → 新建
  3. 全部走 agentops.php 的 db.query / db.exec（X-Ops-Token 鉴权）

token：从环境变量 DISCUZ_OPS_TOKEN 读（GitHub Secrets 注入）；
       本机调试可回退 C:/Users/PC/.workbuddy/secrets/discuz_ops_token.json。
"""
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DISC_BASE = "https://modeai-discuz.ms.show"
BOARD_BASE = "https://modeai-hotboard.ms.show"
TOKEN_FILE = "C:/Users/PC/.workbuddy/secrets/discuz_ops_token.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 Chrome/122.0 Safari/537.36")


def bj(fmt="%Y-%m-%d"):
    """北京时间 = UTC+8（GitHub runner 与容器时钟多为 UTC）。"""
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime(fmt)


def token():
    v = os.environ.get("DISCUZ_OPS_TOKEN")
    if v:
        return v.strip()
    return json.load(open(TOKEN_FILE, encoding="utf-8"))["ops_token"]


def hx(s):
    """MySQL hex 字面量，彻底避开中文/引号/BBCode 的 SQL 转义问题。"""
    return "0x" + str(s or "").encode("utf-8").hex()


def ops(action, body=None, retries=3):
    url = DISC_BASE + "/agentops.php?action=" + urllib.parse.quote(action)
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    last = ""
    for i in range(retries):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "User-Agent": UA, "X-Ops-Token": token(),
            "Content-Type": "application/json", "Accept": "application/json",
            "Connection": "close",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if "Database Error" in raw:
                import re
                msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
                raise RuntimeError("Discuz SQL 错误: " + msg[:220])
            last = "HTTP %s %s" % (e.code, raw[:100])
        except Exception as e:
            last = repr(e)
        if i < retries - 1:
            time.sleep(1.0 + i)
    raise RuntimeError("agentops %s 失败: %s" % (action, last))


def db_query(sql):
    return ops("db.query", {"sql": sql})


def db_exec(sql):
    return ops("db.exec", {"sql": sql})


def resolve_fid():
    if os.environ.get("DISCUZ_FID"):
        return int(os.environ["DISCUZ_FID"])
    # 按版块名定位「每日热榜」，兜底 21（登记表确认 fid=21）
    try:
        for f in ops("forum.list").get("forums", []):
            if "每日热榜" in str(f.get("name")):
                return int(f["fid"])
    except Exception:
        pass
    return 21


def ensure_forum_bbcode(fid):
    try:
        r = db_query("SELECT allowbbcode FROM pre_forum_forum WHERE fid=%d" % int(fid))
        rows = r.get("rows") or []
        cur = int(list(rows[0].values())[0]) if rows else 0
        if cur != 1:
            db_exec("UPDATE pre_forum_forum SET allowbbcode=1,allowsmilies=1,"
                    "allowimgcode=1 WHERE fid=%d" % int(fid))
            return "已开启版块 BBCode（原 %s）" % cur
    except Exception as e:
        return "版块权限检查失败: " + str(e)[:60]
    return ""


def uid_admin():
    try:
        r = db_query("SELECT uid FROM pre_common_member WHERE username='admin' LIMIT 1")
        if r.get("rows"):
            return int(list(r["rows"][0].values())[0])
    except Exception:
        pass
    return 1


def alloc_pid():
    r = db_query("SELECT MAX(pid) AS m FROM pre_forum_post_tableid")
    pid = int(list(r["rows"][0].values())[0]) + 1
    res = db_exec("INSERT INTO pre_forum_post_tableid (pid) VALUES (%d)" % pid)
    if not res.get("ok"):
        raise RuntimeError("pid 分配失败: " + str(res)[:120])
    return pid


def insert_first_post(tid, fid, uid, subject, bbcode):
    pid = alloc_pid()
    author = hx("admin")
    sql = ("INSERT INTO pre_forum_post (pid,fid,tid,first,author,authorid,subject,dateline,message,"
           "useip,port,invisible,anonymous,usesig,htmlon,bbcodeoff,smileyoff,parseurloff,"
           "attachment,rate,ratetimes,status,position) "
           "VALUES (%d,%d,%d,1,%s,%d,%s,UNIX_TIMESTAMP(),%s,'127.0.0.1',0,0,0,1,0,0,0,0,0,0,0,0,1)"
           % (pid, int(fid), int(tid), author, int(uid), hx(subject), hx(bbcode)))
    r = db_exec(sql)
    if not r.get("ok"):
        raise RuntimeError("post insert failed: " + str(r)[:120])
    chk = db_query("SELECT COUNT(*) AS n FROM pre_forum_post WHERE tid=%d AND first=1" % int(tid))
    if int(list(chk["rows"][0].values())[0]) < 1:
        raise RuntimeError("首帖复查失败 tid=%d" % int(tid))
    return pid


def sync_counts(fid):
    t = db_query("SELECT COUNT(*) AS n FROM pre_forum_thread WHERE fid=%d" % int(fid))
    p = db_query("SELECT COUNT(*) AS n FROM pre_forum_post WHERE fid=%d" % int(fid))
    tc = int(list(t["rows"][0].values())[0])
    pc = int(list(p["rows"][0].values())[0])
    db_exec("UPDATE pre_forum_forum SET threads=%d,posts=%d WHERE fid=%d" % (tc, pc, int(fid)))
    return tc, pc


def post_state(day):
    subj = "每日热榜 · %s" % day
    r = db_query("SELECT tid FROM pre_forum_thread WHERE subject=%s ORDER BY tid DESC LIMIT 1" % hx(subj))
    rows = r.get("rows") or []
    if not rows:
        return None, False
    tid = int(list(rows[0].values())[0])
    c = db_query("SELECT COUNT(*) AS n FROM pre_forum_post WHERE tid=%d AND first=1" % tid)
    n = int(list(c["rows"][0].values())[0]) if c.get("rows") else 0
    return str(tid), n > 0


def fetch_payload(day):
    url = BOARD_BASE + "/api/daily-rank?day=" + urllib.parse.quote(day)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Connection": "close"})
    with urllib.request.urlopen(req, timeout=40) as r:
        j = json.load(r)
    return j["payload"]


def publish(day):
    payload = fetch_payload(day)
    subject = payload["subject"]
    bbcode = payload.get("bbcode", "")
    if not bbcode:
        raise RuntimeError("榜单 payload 无 bbcode，数据异常")

    tid, complete = post_state(day)
    if complete:
        return tid, "skip", "当日榜单帖已存在（tid=%s），幂等跳过" % tid

    fid = resolve_fid()
    note = ensure_forum_bbcode(fid)
    uid = uid_admin()
    author = hx("admin")

    if tid:  # 主题在、首帖缺 → 补帖
        insert_first_post(tid, fid, uid, subject, bbcode)
        sync_counts(fid)
        return tid, "repair", "主题存在首帖缺失，已补齐 tid=%s %s" % (tid, note)

    # 新建主题
    sql_thread = (
        "INSERT INTO pre_forum_thread (fid,posttableid,typeid,sortid,readperm,price,"
        "author,authorid,subject,dateline,lastpost,lastposter,views,replies,displayorder,"
        "highlight,digest,rate,special,attachment,moderated,closed,status,isgroup,maxposition) "
        "VALUES (%d,0,0,0,0,0,%s,%d,%s,UNIX_TIMESTAMP(),UNIX_TIMESTAMP(),%s,0,0,0,0,0,0,0,0,0,0,0,0,1)"
        % (fid, author, uid, hx(subject), author))
    r1 = db_exec(sql_thread)
    if not r1.get("ok"):
        raise RuntimeError("thread insert failed: " + str(r1)[:120])
    r2 = db_query("SELECT tid FROM pre_forum_thread WHERE subject=%s ORDER BY tid DESC LIMIT 1" % hx(subject))
    newtid = int(list(r2["rows"][0].values())[0])
    insert_first_post(newtid, fid, uid, subject, bbcode)
    sync_counts(fid)
    return str(newtid), "new", "agentops 发帖成功 fid=%s tid=%s pid已分配 %s" % (fid, newtid, note)


def main():
    day = os.environ.get("HOTBOARD_DAY") or bj()
    print("hotboard daily post: day=%s (BJ) token_len=%d" % (day, len(token())))
    tid, action, detail = publish(day)
    print("RESULT tid=%s action=%s detail=%s" % (tid, action, detail))
    # 供 workflow 判断是否写入状态文件
    print("::notice::热榜帖 day=%s tid=%s %s" % (day, tid, detail))


if __name__ == "__main__":
    main()
