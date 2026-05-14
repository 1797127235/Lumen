"""文本分块器 — 递归字符分割，保留上下文重叠。"""

from __future__ import annotations


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100,
    separators: list[str] | None = None,
) -> list[str]:
    """将文本分块，保持块大小不超过 chunk_size，块间保留 overlap 重叠。

    策略：
    1. 按 separators 递归分割为语义小片
    2. 合并小片为不超过 chunk_size 的 chunks
    3. 每个 chunk（除第一个）开头包含前一个 chunk 末尾 overlap 个字符
    """
    if separators is None:
        separators = ["\n\n", "\n", "。", ".", " ", ""]

    text = text.strip()
    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    def _split(t: str, seps: list[str]) -> list[str]:
        if not seps or len(t) <= chunk_size:
            return [t]
        sep = seps[0]
        parts = t.split(sep)
        result: list[str] = []
        for i, part in enumerate(parts):
            piece = (sep + part) if i > 0 else part
            if len(piece) <= chunk_size:
                result.append(piece)
            else:
                result.extend(_split(piece, seps[1:]))
        return result

    pieces = _split(text, separators)

    # 合并小片为 chunks
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        if not piece.strip():
            continue
        if len(current) + len(piece) <= chunk_size:
            current += piece
        else:
            if current.strip():
                chunks.append(current.strip())
            current = piece

    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        return []

    # 应用 overlap：后一个 chunk 开头包含前一个 chunk 末尾 overlap 字符
    # 同时保证 chunk 总长度不超过 chunk_size
    if overlap > 0 and len(chunks) > 1:
        final: list[str] = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                final.append(chunk)
            else:
                prev_tail = chunks[i - 1][-overlap:]
                merged = prev_tail + chunk
                if len(merged) > chunk_size:
                    merged = merged[:chunk_size]
                final.append(merged)
        chunks = final

    return [c for c in chunks if c.strip()]
