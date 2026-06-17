"""主动送达层 — 把"结果送到哪"从任务/订阅身上解耦。

设计动机:定时任务(scheduler)和事件订阅(triggers)只该表达"做什么"(到点跑工具 /
收到事件),不该知道"结果推给谁、推哪个渠道"。渠道绑定是用户级的事实,不是任务级参数。
本模块是这层抽象:给定 user_id + 内容,投送到该用户可达的渠道。

单用户 + 第一版只 Telegram:
  读 config.json["telegram_chat_id"](用户首次在 Telegram 说话时 _auto_save_chat_id 自动登记),
  非空则注入 InboundMessage 到 "telegram" 渠道 → AgentRunner 跑 agent → TelegramChannel 经 Bot API 推送。

扩展点:未来加 Web 主动推送,只需在本模块内加分支(写持久化收件箱),任务/订阅代码一行不动。
未来支持多用户,把单用户默认 user_id 改成查 user→channels 绑定表即可。
"""

from __future__ import annotations

from shared.logging import get_logger

logger = get_logger(__name__)


async def deliver(
    user_id: str,
    content: str,
    *,
    source: str,
    source_id: str,
) -> list[str]:
    """主动送达:把内容投到用户可达的渠道。

    Args:
        user_id: 触发源记录的用户 id(可能为空,单用户下兜底为默认 user)
        content: 注入对话的内容(会作为 InboundMessage.content,驱动 agent 主动找用户)
        source: 触发来源标记("scheduler" / "trigger"),写入 metadata 便于追溯
        source_id: 任务 id / 订阅 id,写入 metadata 便于追溯

    Returns:
        实际投递成功的渠道名列表(如 ["telegram"]);无可用渠道时为空列表。
    """
    delivered: list[str] = []

    # ── 渠道映射:渠道名 → chat_id ──
    # 单用户多渠道:每个可达渠道一个 chat_id,送达时遍历做 fan-out。
    # 当前只 Telegram(QQ/微信接入时在此加分支,各渠道收到消息时自动登记自己的 chat_id)。
    channels: dict[str, str] = {}
    try:
        from core.config import get_settings

        tg = (get_settings().telegram_chat_id or "").strip()
        if tg:
            channels["telegram"] = tg
    except Exception as exc:
        logger.warning("读取渠道映射失败", error=str(exc))

    # ── fan-out:遍历所有可达渠道 ──
    from lib.bus.queue import InboundMessage, get_bus
    from lib.identity import get_user_id

    bus = get_bus()
    if bus is None:
        logger.warning("MessageBus 未注册,无法送达", source=source, source_id=source_id)
        return delivered

    # sender = 单用户身份常量(决定 agent 用哪份记忆 memory/{user_id}/)。
    # 身份与渠道解耦:不管投到哪个渠道,sender 都是同一个"你"。
    sender = user_id or get_user_id()

    for channel_name, chat_id in channels.items():
        try:
            await bus.publish_inbound(
                InboundMessage(
                    channel=channel_name,
                    sender=sender,
                    chat_id=chat_id,
                    content=content,
                    metadata={"source": source, source: source_id},
                )
            )
            delivered.append(channel_name)
            logger.info(
                "主动送达 → %s",
                channel_name,
                source=source,
                source_id=source_id,
                chat_id=chat_id,
            )
        except Exception as exc:
            logger.error("送达 %s 失败", channel_name, source=source, source_id=source_id, error=str(exc))

    # ── 扩展点:未来接入 QQ/微信 ──
    # 在上面的 channels 字典里加 "qq": qq_chat_id / "wechat": wx_chat_id 即可,
    # fan-out 循环自动覆盖,无需改 deliver 调用方。chat_id 登记机制:
    # 各渠道收到消息时(类似 Telegram 的 _auto_save_chat_id)写入 config 对应字段。

    if not delivered:
        logger.warning(
            "主动送达无可用渠道(未配置任何渠道 chat_id 或 bus 未就绪)",
            source=source,
            source_id=source_id,
            user_id=user_id,
        )
    return delivered
