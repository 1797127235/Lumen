"""RSS 订阅模块 — 异步拉取 + LLM 过滤 + Telegram 推送。

feed_service:  异步拉取、解析、去重、ACK（持久化到 ~/.lumen/rss/ JSON 文件）
scheduler:     后台定时调度（拉取 → LLM 过滤 → Telegram 推送）
filter:        LLM 相关性判断 + FOCUS.md + 关键词兜底
"""
