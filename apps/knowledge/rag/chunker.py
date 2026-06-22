"""文本分块：滑动窗口，支持中英文混合。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ChunkOptions:
    """分块选项。"""

    size: int = 800
    """每块目标字符数（约等于 ~500 token）"""
    overlap: int = 100
    """相邻块重叠字符数"""
    min_size: int = 80
    """单块最小字符数"""


@dataclass
class Chunk:
    """分块结果。"""

    text: str
    index: int
    token_count: int


def is_cjk(ch: str) -> bool:
    """判断是否为 CJK 字符。"""
    code = ord(ch)
    return (
        (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0x20000 <= code <= 0x2A6DF)
        or (0x2A700 <= code <= 0x2B73F)
        or (0x2B740 <= code <= 0x2B81F)
        or (0x2B820 <= code <= 0x2CEAF)
        or (0xF900 <= code <= 0xFAFF)
    )


def approx_token_len(text: str) -> int:
    """近似 token 长度估算，支持中英文混合。

    - CJK 字符按 1 token 计
    - 其他字符按空白分词计
    """
    cjk = 0
    non_cjk_tokens = 0
    in_word = False

    for ch in text:
        if is_cjk(ch):
            cjk += 1
            in_word = False
        elif ch.isspace():
            in_word = False
        else:
            if not in_word:
                non_cjk_tokens += 1
                in_word = True

    return cjk + non_cjk_tokens


def chunk_text(text: str, options: ChunkOptions | None = None) -> list[Chunk]:
    """将长文本切分为带重叠的块。

    算法：
    1. 按空行/换行切成段落
    2. 段落顺序拼接，累积到 >= size 即产出一个块
    3. 新块起点回退 overlap 字符，保证上下文连续
    """
    opts = options or ChunkOptions()
    size = opts.size
    overlap = opts.overlap
    min_size = opts.min_size

    cleaned = text.replace("\r\n", "\n")
    if not cleaned.strip():
        return []

    # 拆段落：连续换行视为分隔
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]

    # 逐段落累加，到阈值产出块
    raw: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        t = buf.strip()
        if t:
            raw.append(t)
        buf = ""

    for para in paragraphs:
        # 单段落超长时，硬切
        if len(para) > size:
            flush()
            for i in range(0, len(para), size - overlap):
                raw.append(para[i : i + size].strip())
            buf = ""
            continue

        # 累加后超阈值则先产出
        if len(buf) + len(para) + 1 > size and len(buf) >= min_size:
            flush()
            # 重叠：把上一块尾部带入新块起点
            tail = raw[-1][-overlap:] if raw else ""
            buf = tail + ("\n" if tail else "") + para
        else:
            buf += ("\n" if buf else "") + para

    flush()

    # 合并过短的尾块到前一块
    merged: list[str] = []
    for r in raw:
        if len(r) < min_size and merged:
            merged[-1] += "\n" + r
        else:
            merged.append(r)

    return [Chunk(text=t, index=i, token_count=approx_token_len(t)) for i, t in enumerate(merged)]
