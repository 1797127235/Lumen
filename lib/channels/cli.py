"""命令行 Channel — 常驻盒子式流式 TUI。

两种用法：
1. 独立应用（推荐）：`python -m lib.channels.cli`
   自起 MessageBus + EventBus + AgentRunner，无需运行后端服务。
2. 作为 Channel：被外部 bootstrap 创建并 start()，订阅 cli 出站/流式事件。

架构（对齐 hermes）：
- 一个常驻 prompt_toolkit Application 跑整个会话，状态栏 + 输入框始终钉在底部。
- agent 在后台运行；回显/工具/回答通过 run_in_terminal 打到 App 上方的滚动区。
- 回答逐行流式（行缓冲：每遇换行打一行），思考时底部 slot 显示 spinner。
- token 顺序依赖 EventBus 同步 handler（emit 内联调用）+ 单 print_queue + printer 任务串行输出。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from typing import Any, ClassVar

from prompt_toolkit.completion import Completer, Completion

from lib.bus.event_bus import (
    EventBus,
    StreamDeltaReady,
    ToolCallCompleted,
    ToolCallStarted,
)
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.channels.base import BaseChannel

logger = logging.getLogger(__name__)

_USER_ID = "demo_user"  # 与 Web 端默认 user_id 一致，共享同一份记忆/画像
_DOTS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # 思考 spinner 帧


class CLIChannel(BaseChannel):
    """命令行 Channel（常驻盒子流式 TUI）。"""

    SLASH_COMMANDS: ClassVar[dict[str, str]] = {
        "/help": "显示帮助",
        "/clear": "清屏",
        "/new": "开始新会话",
        "/quit": "退出",
        "/exit": "退出",
    }

    def __init__(self, bus: MessageBus, event_bus: EventBus) -> None:
        self._bus = bus
        self._event_bus = event_bus
        self._running = False
        self._chat_id = self._new_chat_id()
        # ── 输出/渲染 ──
        self._print_queue: asyncio.Queue[tuple | None] | None = None
        self._app: Any = None
        self._console: Any = None
        self._agent_running = False
        self._answer_started = False
        self._line_buf = ""
        self._think_buf = ""
        # ── 状态栏数据 ──
        self._session_start = time.monotonic()
        self._turn_count = 0
        self._last_turn_secs: float | None = None
        self._ctx_used: int | None = None
        self._ctx_limit = 0
        self._ctx_task: asyncio.Task | None = None
        self._turn_start: float | None = None
        self._tip = ""

    # ── BaseChannel 接口 ──────────────────────────────────

    async def start(self) -> None:
        """订阅出站消息 + 流式事件。不在此启动输入循环。"""
        self._running = True
        self._bus.subscribe_outbound("cli", self._on_response)
        # 同步 handler：emit 内联调用，保证 token 顺序
        self._event_bus.on(StreamDeltaReady, self._on_delta)
        self._event_bus.on(ToolCallStarted, self._on_tool_call)
        self._event_bus.on(ToolCallCompleted, self._on_tool_done)
        logger.info("CLIChannel started")

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, chat_id: str, content: str, **kwargs) -> None:
        # 流式路径不走这里，仅作兜底
        print(f"Lumen: {content}")

    # ── 事件 handler（按 channel 过滤 → 推入 print_queue）──────

    def _push(self, job: tuple) -> None:
        q = self._print_queue
        if q is not None:
            q.put_nowait(job)

    def _on_delta(self, event: StreamDeltaReady) -> None:
        if event.channel != "cli":
            return
        # 思考过程：只累积，显示在 App 临时区域（阅后即焚，不进滚动历史）
        if event.thinking_delta:
            self._think_buf += event.thinking_delta
        # 回答开始：标记后思考临时区自动清空，正文流入滚动区
        if event.content_delta:
            self._answer_started = True
            self._line_buf += event.content_delta
            while "\n" in self._line_buf:
                line, self._line_buf = self._line_buf.split("\n", 1)
                self._push(("line", line))

    def _on_tool_call(self, event: ToolCallStarted) -> None:
        if event.channel != "cli":
            return
        self._push(("tool_call", event.tool_name, _compact_args(event.arguments)))

    def _on_tool_done(self, event: ToolCallCompleted) -> None:
        if event.channel != "cli":
            return
        self._push(("tool_result", event.status, event.result_preview))

    async def _on_response(self, msg: OutboundMessage) -> None:
        """最终回复 → 冲掉残余行 + 收尾。"""
        if msg.chat_id != self._chat_id:
            return
        usage = (msg.metadata or {}).get("usage") or {}
        elapsed = (time.monotonic() - self._turn_start) if self._turn_start else None
        self._push(("done", self._line_buf, usage.get("input"), elapsed, msg.content))
        self._line_buf = ""

    # ── 交互循环 ───────────────────────────────────────────

    async def run_interactive(self) -> None:
        """启动 banner + 常驻输入 App（独立 app 调用）。"""
        import contextlib

        from rich.console import Console

        from core.config import get_settings

        self._console = Console()
        settings = get_settings()
        self._session_start = time.monotonic()
        # 上下文窗口：配置优先；否则后台从 models.dev 解析（不阻塞启动，解析到再刷新状态栏）
        cfg_limit = getattr(settings, "llm_context_limit", 0) or 0
        self._ctx_limit = cfg_limit
        if not cfg_limit:
            self._ctx_task = asyncio.create_task(self._resolve_ctx_limit_bg(settings.llm_model))
        self._tip = random.choice(_TIPS)
        self._print_queue = asyncio.Queue()
        self._print_banner(self._console)
        self._app = self._build_app()

        printer = asyncio.create_task(self._printer_loop())
        ticker = asyncio.create_task(self._ui_tick_loop())
        try:
            await self._app.run_async()
        finally:
            self._running = False
            self._push(None)  # 唤醒 printer
            printer.cancel()
            ticker.cancel()
            tasks = [printer, ticker] + ([self._ctx_task] if self._ctx_task else [])
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._console.print("\n[dim]👋 再见。[/dim]")

    async def _resolve_ctx_limit_bg(self, model: str) -> None:
        """后台解析模型上下文窗口（models.dev，带磁盘缓存），解析到再刷新状态栏。"""
        import contextlib

        with contextlib.suppress(Exception):
            limit = await _resolve_context_limit(model, 0)
            if limit:
                self._ctx_limit = limit
                if self._app is not None:
                    self._app.invalidate()

    def _build_app(self):
        """常驻 prompt_toolkit Application：状态栏 + 输入框钉在底部，被横线夹成盒子。"""
        from prompt_toolkit.application import Application
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, HSplit, Layout, Window
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.styles import Style
        from prompt_toolkit.widgets import TextArea

        from core.config import USER_DATA_DIR

        def _accept(buff) -> bool:
            if not self._agent_running and self._app is not None:
                self._app.create_background_task(self._submit(buff.text))
            return False  # 清空输入

        input_area = TextArea(
            height=Dimension(min=1, max=6, preferred=1),
            prompt=[("class:prompt", "❯ ")],
            multiline=False,  # Enter 提交
            wrap_lines=True,
            read_only=Condition(lambda: self._agent_running),
            history=FileHistory(str(USER_DATA_DIR / "cli_history")),
            completer=_SlashCompleter(self.SLASH_COMMANDS),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            accept_handler=_accept,
            style="class:input-area",
        )

        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-d")
        def _(event):
            event.app.exit()

        def _rule():
            return Window(char="─", height=1, style="class:rule")

        def _thinking_visible() -> bool:
            return self._agent_running and not self._answer_started and bool(self._think_buf.strip())

        # 思考临时区：ConditionalContainer 保证不可见时整块 0 高度（彻底塌缩，不留空洞）
        thinking_widget = ConditionalContainer(
            Window(
                FormattedTextControl(self._thinking_fragments),
                height=Dimension(min=0, max=8),
                wrap_lines=True,
                style="class:think-text",
            ),
            filter=Condition(_thinking_visible),
        )

        layout = Layout(
            HSplit(
                [
                    thinking_widget,
                    Window(FormattedTextControl(self._top_line), height=1),
                    _rule(),
                    Window(FormattedTextControl(self._status_fragments), height=1, wrap_lines=False),
                    _rule(),
                    input_area,
                    _rule(),
                ]
            ),
            focused_element=input_area,
        )

        style = Style.from_dict(
            {
                "rule": "fg:#d2691e",
                "tip-star": "fg:#e5c07b",
                "tip": "fg:#98c379",
                "think": "fg:#e5c07b italic",
                "think-text": "fg:#5c6370 italic",
                "prompt": "fg:#61afef bold",
                "st-model": "fg:#61afef bold",
                "st-sep": "fg:#5c6370",
                "st-label": "fg:#abb2bf",
                "st-fill": "fg:#56b6c2",
                "st-empty": "fg:#3a3f4b",
            }
        )

        return Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
        )

    async def _submit(self, text: str) -> None:
        """处理一次输入：回显 + slash 或发往 agent。"""
        line = text.strip()
        if self._agent_running or not line:
            return
        self._push(("echo", line))
        if line.startswith("/"):
            cmd = line.split()[0].lower()
            if cmd in ("/quit", "/exit"):
                if self._app:
                    self._app.exit()
            elif cmd == "/help":
                self._push(("help",))
            elif cmd == "/clear":
                self._push(("clear",))
            elif cmd == "/new":
                self._chat_id = self._new_chat_id()
                self._push(("info", "已开始新会话。"))
            else:
                self._push(("info", f"未知命令：{cmd}"))
            return
        self._agent_running = True
        self._answer_started = False
        self._line_buf = ""
        self._think_buf = ""
        self._turn_start = time.monotonic()
        if self._app:
            self._app.invalidate()
        await self._bus.publish_inbound(
            InboundMessage(channel="cli", sender=_USER_ID, chat_id=self._chat_id, content=line)
        )

    async def _printer_loop(self) -> None:
        """串行消费 print_queue，用 run_in_terminal 把内容打到 App 上方。"""
        from prompt_toolkit.application import run_in_terminal

        while self._running:
            job = await self._print_queue.get()
            if job is None:
                break
            try:
                await run_in_terminal(lambda j=job: self._emit_job(j))
            except Exception:
                # 记到日志文件（不污染 TUI），printer 继续存活；CancelledError 不属 Exception，正常传播
                logger.exception("CLI 输出任务失败: %r", job)
            if self._app is not None and getattr(self._app, "is_running", False):
                self._app.invalidate()

    async def _ui_tick_loop(self) -> None:
        """周期性重绘：思考时刷 spinner，空闲时让会话时长跳秒。"""
        while self._running:
            if self._app is not None and getattr(self._app, "is_running", False):
                self._app.invalidate()
            await asyncio.sleep(0.1 if self._agent_running else 1.0)

    def _emit_job(self, job: tuple) -> None:
        """在 run_in_terminal 内执行：把一条内容打到 App 上方滚动区。"""
        from rich.markdown import Markdown
        from rich.text import Text

        console = self._console
        kind = job[0]
        if kind == "echo":
            console.print(Text.assemble(("❯ ", "bold #61afef"), (job[1], "")))
        elif kind == "line":
            console.print(Text(_strip_markdown(job[1])))
        elif kind == "tool_call":
            t = Text("🔧 ", style="cyan")
            t.append(job[1], style="cyan")
            if job[2]:
                t.append(f"  {job[2]}", style="dim")
            console.print(t)
        elif kind == "tool_result":
            ok = job[1] == "done"
            t = Text("  ✓ " if ok else "  ✗ ", style="green" if ok else "red")
            t.append(job[2] or "", style="dim")
            console.print(t)
        elif kind == "done":
            _, remainder, ctx_used, elapsed, full = job
            if self._answer_started:
                if remainder.strip():
                    console.print(Text(_strip_markdown(remainder)))
            elif full:
                console.print(Markdown(full))  # 未流式（如错误信息）→ 渲染 Markdown
            console.print()
            if ctx_used is not None:
                self._ctx_used = ctx_used
            if elapsed is not None:
                self._last_turn_secs = elapsed
            self._turn_count += 1
            self._agent_running = False
            self._answer_started = False
            self._tip = random.choice(_TIPS)
        elif kind == "help":
            self._print_help(console)
        elif kind == "clear":
            console.clear()
            self._print_banner(console)
        elif kind == "info":
            console.print(Text.assemble(("✦ ", "dim"), (job[1], "dim")))

    # ── 底部盒子内容 ───────────────────────────────────────

    def _thinking_fragments(self):
        """思考临时区内容：思考时显示最近若干行，回答开始/空闲时为空（即消失）。"""
        if not self._agent_running or self._answer_started or not self._think_buf.strip():
            return []
        tail = "\n".join(self._think_buf.strip().splitlines()[-8:])
        return [("class:think-text", tail)]

    def _top_line(self):
        """盒子顶行：思考时 spinner，回答中留空，空闲显示 Tip。"""
        if self._agent_running and not self._answer_started:
            frame = _DOTS[int(time.monotonic() * 10) % len(_DOTS)]
            return [("class:think", f"  {frame} 🧠 思考中…")]
        if self._agent_running:
            return []  # 回答中，思考已结束
        return self._tip_fragments()

    def _tip_fragments(self):
        return [("class:tip-star", "  ✦ "), ("class:tip", f"Tip: {self._tip}")]

    def _status_fragments(self):
        """状态栏单行 fragments：模型 | ctx 进度条 | 计时器。"""
        from core.config import get_settings

        model = get_settings().llm_model or "?"
        used = self._ctx_used
        limit = self._ctx_limit or 0
        used_s = _fmt_tokens(used) if used is not None else "--"
        limit_s = _fmt_tokens(limit) if limit else "?"
        cells = 6
        filled = max(0, min(cells, round((used / limit) * cells))) if used and limit else 0
        pct = f"{round(used / limit * 100)}%" if used and limit else "--"
        uptime = _fmt_duration(time.monotonic() - self._session_start)
        latency = _fmt_latency(self._last_turn_secs)

        sep = ("class:st-sep", "  │  ")
        return [
            ("class:st-model", f"  🌙 {model}"),
            sep,
            ("class:st-label", f"ctx {used_s}/{limit_s} "),
            ("class:st-sep", "["),
            ("class:st-fill", "▓" * filled),
            ("class:st-empty", "░" * (cells - filled)),
            ("class:st-sep", "] "),
            ("class:st-label", pct),
            sep,
            ("class:st-label", f"⏱ {uptime}"),
            sep,
            ("class:st-label", f"🌐 {latency}"),
        ]

    # ── 静态打印 ───────────────────────────────────────────

    def _print_help(self, console) -> None:
        from rich.table import Table

        table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        for cmd, desc in self.SLASH_COMMANDS.items():
            table.add_row(f"[cyan]{cmd}[/cyan]", desc)
        console.print(table)
        console.print()

    def _print_banner(self, console) -> None:
        from rich.panel import Panel
        from rich.text import Text

        from core.config import get_settings

        settings = get_settings()
        model = f"{settings.llm_provider} · {settings.llm_model}"
        body = Text()
        body.append("🌙 Lumen\n", style="bold magenta")
        body.append("一个真正认识你的 AI 伙伴\n\n", style="dim")
        body.append("模型  ", style="dim")
        body.append(f"{model}\n", style="cyan")
        body.append("命令  ", style="dim")
        body.append("/help  /new  /clear  /quit", style="cyan")
        console.print(Panel(body, border_style="magenta", padding=(1, 3)))
        console.print()

    @staticmethod
    def _new_chat_id() -> str:
        return f"cli-{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════════


def _strip_markdown(text: str) -> str:
    """逐行剥掉 markdown 标记，流式纯文本更干净（best-effort，移植自 hermes）。

    保留引用/列表/勾选框等结构性前缀，仅去掉行内/标题/分隔线等装饰标记。
    """
    t = text
    t = re.sub(r"^\s{0,3}(?:[-*_]\s*){3,}$", "", t, flags=re.MULTILINE)  # 分隔线 ---
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", t, flags=re.MULTILINE)  # 标题井号
    t = re.sub(r"(```+|~~~+)", "", t)  # 代码围栏标记
    t = re.sub(r"`([^`]*)`", r"\1", t)  # 行内代码
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)  # 图片
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # 链接
    t = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", t)  # 粗斜
    t = re.sub(r"(?<!\w)___([^_]+)___(?!\w)", r"\1", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)  # 粗体
    t = re.sub(r"(?<!\w)__([^_]+)__(?!\w)", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)  # 斜体
    t = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", t)
    return re.sub(r"~~([^~]+)~~", r"\1", t)  # 删除线


def _compact_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    import json

    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s if len(s) <= 80 else s[:77] + "…"


# 模型上下文窗口：不硬编码——从 models.dev（社区模型元数据库）解析真实值并缓存到磁盘。
# 参考 hermes 的 models_dev 机制。优先级：配置 > models.dev > 未知（0 → 状态栏显示 ?）。
_MODELS_DEV_URL = "https://models.dev/api.json"
_MODELS_DEV_TTL = 86400  # 磁盘缓存 24h


def _models_dev_cache_path():
    from core.config import USER_DATA_DIR

    return USER_DATA_DIR / "models_dev_cache.json"


async def _load_models_dev() -> dict:
    """磁盘缓存(24h) → 联网拉取 → 缓存；失败回退陈旧缓存或 {}。"""
    import contextlib
    import json

    path = _models_dev_cache_path()
    try:
        if path.exists() and time.time() - path.stat().st_mtime < _MODELS_DEV_TTL:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_MODELS_DEV_URL)
            resp.raise_for_status()
            data = resp.json()
        with contextlib.suppress(OSError):
            path.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception:
        try:  # 联网失败 → 用陈旧缓存兜底
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {}


def _context_from_models_dev(data: dict, model: str) -> int:
    """跨 provider 按 model id 查 limit.context（model 可能挂在聚合商下，不依赖 provider 名）。"""
    if not data or not model:
        return 0
    for prov in data.values():
        if not isinstance(prov, dict):
            continue
        models = prov.get("models")
        if isinstance(models, dict) and model in models:
            ctx = ((models[model] or {}).get("limit") or {}).get("context")
            if isinstance(ctx, int) and ctx > 0:
                return ctx
    return 0


async def _resolve_context_limit(model: str, config_limit: int) -> int:
    """配置优先；否则查 models.dev。未知返回 0（状态栏显示 ?）。"""
    if config_limit and config_limit > 0:
        return config_limit
    return _context_from_models_dev(await _load_models_dev(), model)


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n // 1000}k" if n % 1000 == 0 else f"{n / 1000:.1f}k"
    m = n / 1_000_000
    return f"{int(m)}M" if n % 1_000_000 == 0 else f"{m:.1f}M"


def _fmt_duration(secs: float) -> str:
    s = int(secs)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def _fmt_latency(secs: float | None) -> str:
    if secs is None:
        return "--"
    return f"{secs:.1f}s" if secs < 60 else _fmt_duration(secs)


_TIPS: list[str] = [
    "直接把重要的事告诉 Lumen，它会记住，下次对话依旧记得。",
    "输入 /new 开始一段全新会话；/clear 清屏；/quit 退出。",
    "问题里给出越多背景，Lumen 的回答越贴合你。",
    "Lumen 可以调用工具查资料、读文件——尽管开口。",
    "随时按 Ctrl+C 或输入 /quit 离开。",
    "状态栏的 ctx 显示本轮占用的上下文 token。",
]


class _SlashCompleter(Completer):
    """仅当输入以 / 开头时补全 slash 命令。"""

    def __init__(self, commands: dict[str, str]) -> None:
        self._commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for cmd, desc in self._commands.items():
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display=cmd, display_meta=desc)


# ═══════════════════════════════════════════════════════════════
#  独立应用入口
# ═══════════════════════════════════════════════════════════════


def _ensure_utf8_stdio() -> None:
    """Windows 控制台默认 GBK，emoji/中文会触发 UnicodeEncodeError，强制 UTF-8。"""
    import contextlib
    import sys

    for stream in (sys.stdout, sys.stdin, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def _configure_quiet_logging() -> None:
    """配置安静日志：保留文件日志，剥离 stdout handler，避免污染 TUI。"""
    from shared.logging import setup_logging

    setup_logging(json_logs=False, log_level="WARNING")
    root = logging.getLogger()
    root.handlers = [
        h for h in root.handlers if not (isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename"))
    ]
    _silence_sqlalchemy()


def _silence_sqlalchemy() -> None:
    """init_db 用 echo=settings.debug，debug 默认 True 会把 SQL 日志刷到屏幕，强制压回 WARNING。"""
    for name in (
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
        "sqlalchemy.orm",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


async def _bootstrap_resources() -> None:
    """复刻 lifespan 的运行时初始化（去掉 FastAPI / Web / Telegram）。"""
    from core.config import apply_user_config, get_settings
    from core.db import Base, get_engine, init_db
    from core.migrations import migrate_md_files, migrate_sqlite
    from lib import model_registry  # noqa: F401  确保模型注册到 Base.metadata

    init_db(echo=False)  # 关闭 SQL echo，避免刷屏（debug=True 时默认会开）
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url):
            await migrate_sqlite(conn)

    await migrate_md_files()
    apply_user_config(get_settings())

    import contextlib

    with contextlib.suppress(Exception):
        from lib.tools.mcp.client_manager import get_mcp_manager

        await get_mcp_manager().connect_all()


async def main() -> None:
    import contextlib
    import warnings

    # 压掉 requests 的良性依赖版本警告（urllib3/chardet 版本不匹配），要在重依赖导入前设
    warnings.filterwarnings("ignore", message=r".*doesn't match a supported version.*")

    from core.db import get_engine
    from lib.chat.agent_runner import AgentRunner

    _ensure_utf8_stdio()
    _configure_quiet_logging()
    await _bootstrap_resources()

    bus = MessageBus()
    event_bus = EventBus()
    runner = AgentRunner(bus, event_bus)
    runner.start()
    dispatch_task = asyncio.create_task(bus.dispatch_outbound())

    channel = CLIChannel(bus, event_bus)
    await channel.start()

    try:
        await channel.run_interactive()
    finally:
        await channel.stop()
        await runner.stop()
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task
        with contextlib.suppress(Exception):
            from lib.tools.mcp.client_manager import get_mcp_manager

            await get_mcp_manager().disconnect_all()
        with contextlib.suppress(Exception):
            await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
