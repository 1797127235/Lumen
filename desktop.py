"""CareerOS Desktop — PyWebView 桌面启动器"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent


def _start_backend(port: int) -> None:
    import uvicorn

    uvicorn.run(
        "app.backend.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


def main():
    port = 8000
    url = f"http://127.0.0.1:{port}"

    # 1. 在后台线程启动后端（同进程，避免 spawn 开销）
    t = threading.Thread(target=_start_backend, args=(port,), daemon=True)
    t.start()

    # 2. 等待后端就绪（最长 30 秒）
    print("正在启动后端...")
    for i in range(60):
        try:
            r = urllib.request.urlopen(f"{url}/api/health", timeout=1)
            if r.status == 200:
                print(f"后端就绪 ({i * 0.5:.1f}s)")
                break
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            time.sleep(0.5)
    else:
        print("后端启动超时！")
        return

    # 3. 打开桌面窗口
    webview.create_window(
        "CareerOS · 码路领航",
        url,
        width=1100,
        height=750,
        min_size=(800, 600),
        text_select=True,
        background_color="#1a1814",
    )
    webview.start()
    # 窗口关闭后 daemon 线程自动退出


if __name__ == "__main__":
    main()
