from __future__ import annotations

import asyncio
import logging

from lib.bus.event_bus import EventBus
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.channels.base import BaseChannel

logger = logging.getLogger(__name__)


class CLIChannel(BaseChannel):
    """命令行 Channel"""

    def __init__(self, bus: MessageBus, event_bus: EventBus) -> None:
        self._bus = bus
        self._event_bus = event_bus
        self._running = False

    async def start(self) -> None:
        """启动 stdin 读取"""
        self._running = True
        self._bus.subscribe_outbound("cli", self._on_response)
        asyncio.create_task(self._read_stdin())
        logger.info("CLIChannel started")

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, chat_id: str, content: str, **kwargs) -> None:
        print(f"AI: {content}")

    async def _read_stdin(self) -> None:
        """读取 stdin"""
        import sys

        print("🌙 Lumen CLI Mode")
        print("Type 'exit' or '/quit' to quit, '/help' for commands.")
        print("─" * 40)

        while self._running:
            try:
                # 使用 asyncio.to_thread 避免阻塞事件循环
                loop = asyncio.get_event_loop()
                line = await loop.run_in_executor(None, sys.stdin.readline)
                line = line.strip()

                if line.lower() in ["exit", "/quit", "/exit"]:
                    print("👋 再见！")
                    self._running = False
                    break

                if not line:
                    continue

                # 处理内置命令
                if await self._handle_command(line):
                    continue

                # 发送到 Bus
                await self._bus.publish_inbound(
                    InboundMessage(
                        channel="cli",
                        sender="user",
                        chat_id="cli",
                        content=line,
                    )
                )

            except EOFError:
                break
            except KeyboardInterrupt:
                print("\n👋 再见！")
                break
            except Exception as e:
                logger.error(f"CLI read error: {e}")

    async def _handle_command(self, line: str) -> bool:
        """处理内置命令，返回 True 表示已处理"""
        if not line.startswith("/"):
            return False

        parts = line.split()
        cmd = parts[0].lower()

        if cmd == "/help":
            print("【命令列表】")
            print("  /help     - 显示帮助")
            print("  /quit     - 退出")
            print("  /memory   - 查看记忆（TODO）")
            print("  /history  - 查看历史（TODO）")
            print("  /clear    - 清空上下文（TODO）")
            return True

        return False

    async def _on_response(self, msg: OutboundMessage) -> None:
        print(f"AI: {msg.content}\n")
