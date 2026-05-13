"""Jittered 指数退避 — 防止并发重试风暴。"""

from __future__ import annotations

import asyncio
import random


async def jittered_sleep(
    attempt: int,
    base: float = 5.0,
    max_delay: float = 120.0,
    jitter: float = 0.5,
) -> None:
    """在重试前等待带抖动的退避时间。

    公式：min(base * 2^(attempt-1), max_delay) + random(0, jitter_ratio * delay)
    """
    delay = min(base * (2 ** (attempt - 1)), max_delay)
    delay += random.uniform(0, jitter * delay)
    await asyncio.sleep(delay)
