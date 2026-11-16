# 🛡️ 魔搭空间保活

盛通 AI 工具台的魔搭创空间（ms.show）无人访问会休眠。本仓库通过 **浏览器式 GET 访问** 触发自动冷启动保温，**不调用任何发布/部署接口**。

## 三个入口，同一份配置

配置核心都是 [`keepalive.json`](keepalive.json) 里的 `projects` 数组：

| 入口 | 适用场景 | 说明 |
|------|---------|------|
| **GitHub Actions**（推荐） | 全自动、不依赖你的电脑 | 每 6 小时自动巡检一次，云端运行，关机也照跑 |
| **可视化管理页** [`index.html`](index.html) | 想增删/改链接 | 浏览器打开，填 PAT 令牌，点「保存到 GitHub」直写回仓库 |
| **本地脚本** `scripts/keepalive.py` | 手动跑一次 | `python3 scripts/keepalive.py`

## 保活状态判断

- `200` → 在线保温成功
- `404 / 502 / 503` → 已发出唤醒信号，1-2 分钟后自动冷启动
- `401 / 403` → 已下线或受限，需要去魔搭平台手动点一次「发布」

## 怎么改清单

1. **方式一（推荐）**：打开 `index.html` → 填 GitHub PAT（需 repo 权限）→ 编辑表格 → 点「保存到 GitHub」。定时任务下次自动用新清单。
2. **方式二**：直接编辑 `keepalive.json` 的 `projects`，提交推送即可。

## 定时说明

`.github/workflows/keepalive.yml` 里 cron 为 UTC `0 */6 * * *`，即北京时间 08:00 / 14:00 / 20:00 / 02:00。可在 GitHub 仓库 **Actions** 页手动点「Run workflow」立即保活一次，运行日志和 `keepalive.log` 可在 Actions 产物里查看。

> 注：GitHub 对长期无活动的仓库会暂停定时任务。本仓库保持少量提交即可；若发现停摆，去 Actions 页手动跑一次会重新激活。
