"""单用户身份 — 全系统唯一的"用户是谁"真相源。

Lumen 是单用户(可能多渠道)产品。"用户身份"是一个固定常量,与渠道解耦:
不管用户从 Telegram / QQ / 微信 哪个渠道说话,记忆/会话/消息归属都是同一份。

设计决策(单用户多渠道):
- 身份常量 SINGLE_USER_ID = "me",语义是"你这个人",不绑任何渠道的 chat_id。
- 渠道→chat_id 映射是通信层的事(送达层遍历),不泄漏到身份层。
- 权限校验(conv.user_id != sender)在单用户下无意义,已移除(见 persistence.py)。
- 多用户化时,把 get_user_id() 改成按请求/会话解析,调用点不变。

历史:曾用 telegram_chat_id 兼任身份,导致送达层崩溃(身份与渠道绑死)。
现彻底分离:身份是常量,渠道是映射。
"""

from __future__ import annotations

# 单用户的固定身份标识。语义:"你这个人",与渠道无关。
SINGLE_USER_ID = "me"


def get_user_id() -> str:
    """返回单用户身份常量。多用户化时改为按上下文解析。"""
    return SINGLE_USER_ID
