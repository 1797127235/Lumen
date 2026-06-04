"""Lumen 统一启动入口。

用法:
    python lumen.py              # 默认：web 模式（FastAPI + WebChannel）
    python lumen.py --mode cli   # CLI TUI（自动启动后端 + TUI 界面）
    python lumen.py --mode all   # 全部 channel（Web + Telegram + CLI）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 启动时加载 .env，确保 TELEGRAM_BOT_TOKEN 等变量可用
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lumen — 你的长期个人 AI 伙伴")
    parser.add_argument(
        "--mode",
        choices=["web", "cli", "telegram", "all"],
        default="web",
        help="启动模式：web=仅 Web, cli=CLI TUI, telegram=仅 Telegram, all=全部",
    )
    parser.add_argument("--port", type=int, default=8000, help="后端端口（默认 8000）")
    parser.add_argument("--host", default="127.0.0.1", help="后端监听地址")
    return parser.parse_args()


def _is_port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """等待端口可用，最多等 timeout 秒。"""
    import socket
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def run_cli(port: int) -> None:
    """启动 CLI TUI：先启动后端，再启动 TUI。"""
    bun_path = shutil.which("bun")
    if not bun_path:
        print("  [ERROR] bun 未安装。请先安装: https://bun.sh")
        sys.exit(1)

    cli_dir = Path(__file__).parent / "channels" / "cli"

    if not (cli_dir / "node_modules").exists():
        print("  [CLI] 安装依赖...")
        subprocess.run([bun_path, "install"], cwd=str(cli_dir), check=True)

    # 若后端尚未运行，在后台启动它
    backend_proc = None
    if not _is_port_in_use(port):
        print(f"  [CLI] 启动后端 (port {port})...")
        backend_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(Path(__file__).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not _wait_for_port(port):
            print("  [ERROR] 后端启动超时")
            backend_proc.terminate()
            sys.exit(1)
        print("  [CLI] 后端就绪 ✓")

    print("  [CLI] 启动 TUI...")
    env = {**os.environ, "LUMEN_PORT": str(port)}
    try:
        proc = subprocess.run(
            [bun_path, "run", "dev"],
            cwd=str(cli_dir),
            env=env,
        )
        exit_code = proc.returncode
    finally:
        if backend_proc is not None:
            backend_proc.terminate()

    sys.exit(exit_code)


def run_web(host: str, port: int) -> None:
    """启动 Web 模式（FastAPI + WebChannel）。"""
    from asyncio import run

    import uvicorn

    async def serve():
        config = uvicorn.Config("main:app", host=host, port=port, log_level="info")
        await uvicorn.Server(config).serve()

    run(serve())


def run_telegram(host: str, port: int) -> None:
    """启动 Telegram 模式。"""
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("  [ERROR] 请设置 TELEGRAM_BOT_TOKEN 环境变量")
        sys.exit(1)
    run_web(host, port)


def run_all(host: str, port: int) -> None:
    """启动全部 channel。"""
    import asyncio

    import uvicorn

    async def serve():
        config = uvicorn.Config("main:app", host=host, port=port, log_level="info")
        await uvicorn.Server(config).serve()

    asyncio.run(serve())


def main() -> None:
    args = parse_args()
    mode = args.mode

    print()
    print("  ✦ Lumen ✦")
    print("  ─────────")
    print(f"  模式: {mode}")
    if mode != "cli":
        print(f"  地址: {args.host}:{args.port}")
    print()

    if mode == "cli":
        run_cli(args.port)
    elif mode == "telegram":
        run_telegram(args.host, args.port)
    elif mode == "all":
        run_all(args.host, args.port)
    else:
        run_web(args.host, args.port)


if __name__ == "__main__":
    main()
