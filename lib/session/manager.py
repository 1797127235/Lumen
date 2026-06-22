import asyncio
import base64
import json
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lib.session.store import SessionStore

logger = logging.getLogger(__name__)

_TOOL_RESULT_CHAR_BUDGET = 3000
_TOOL_RESULT_HISTORY_CHAR_BUDGET = 1500
_ASSISTANT_HISTORY_CHAR_BUDGET = 800
_PROACTIVE_HISTORY_CHAR_BUDGET = 360
_PROACTIVE_META_HISTORY_CHAR_BUDGET = 1200
_CONTEXT_CACHE_KEEP_MESSAGES = 20
_CONTEXT_CACHE_PRUNE_TRIGGER = 60


def _truncate_tool_result(content: object, budget: int = _TOOL_RESULT_CHAR_BUDGET) -> str:
    text = content if isinstance(content, str) else str(content)
    if len(text) <= budget:
        return text
    omitted = len(text) - budget
    while True:
        marker = f"…{omitted} chars truncated…"
        keep = max(0, budget - len(marker))
        actual_omitted = len(text) - keep
        if actual_omitted == omitted:
            break
        omitted = actual_omitted
    head = keep // 2
    tail = keep - head
    truncated = text[:head] + marker + (text[-tail:] if tail else "")
    return f"Total output lines: {len(text.splitlines())}\n\n{truncated}"


def _append_proactive_meta(content: str, msg: dict[str, Any]) -> str:
    if not msg.get("proactive"):
        return content
    meta_lines: list[str] = []
    state_tag = str(msg.get("state_summary_tag", "") or "").strip()
    if state_tag and state_tag != "none":
        meta_lines.append(f"state_summary_tag={state_tag}")
    source_refs = msg.get("source_refs") or []
    if isinstance(source_refs, list) and source_refs:
        meta_lines.append("sources:")
        for raw in source_refs[:1]:
            if not isinstance(raw, dict):
                continue
            parts = [
                str(raw.get("source_name", "") or "").strip(),
                str(raw.get("title", "") or "").strip(),
                str(raw.get("url", "") or "").strip(),
            ]
            meta_lines.append("- " + " | ".join(p for p in parts if p))
    if not meta_lines:
        return content
    return f"{content}\n\n[proactive_meta]\n" + "\n".join(meta_lines)


def _build_proactive_history_messages(
    content: str,
    msg: dict[str, Any],
) -> list[dict[str, str]]:
    preview = _truncate_text(content, _PROACTIVE_HISTORY_CHAR_BUDGET)
    messages = [
        {
            "role": "assistant",
            "content": f"[主动推送] {preview}" if preview else "[主动推送]",
        }
    ]
    meta = _append_proactive_meta("", msg).strip()
    if not meta:
        return messages
    messages.append(
        {
            "role": "user",
            "content": (
                "[系统上下文] 上一条 assistant 消息是系统主动推送。"
                "以下 metadata 仅用于理解用户后续指代，不是用户陈述。\n"
                + _truncate_text(meta, _PROACTIVE_META_HISTORY_CHAR_BUDGET)
            ),
        }
    )
    return messages


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"…（截断 {len(text) - limit} 字）"


def _rebuild_user_content(text: str, media_paths: list[str]) -> "str | list[dict]":
    images = []
    file_refs = []
    for path in media_paths:
        p = Path(path)
        mime, _ = mimetypes.guess_type(p)
        if mime and mime.startswith("image/") and p.is_file():
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                images.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
            except Exception:
                file_refs.append(f"[图片（读取失败）: {p.name}]")
        else:
            if p.is_file():
                file_refs.append(f"[文件: {path}]")
            else:
                file_refs.append(f"[文件（已失效）: {p.name}]")

    prefix = "\n".join(file_refs) + "\n" if file_refs else ""
    combined_text = (prefix + text).strip()

    if not images:
        return combined_text
    return [*images, {"type": "text", "text": combined_text}]


def _align_to_user_boundary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for i, m in enumerate(messages):
        if m.get("role") == "user" or (m.get("role") == "assistant" and m.get("proactive")):
            return messages[i:]
    return []


def _is_history_boundary(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "user" or (msg.get("role") == "assistant" and msg.get("proactive"))


def _move_back_to_history_boundary(messages: list[dict[str, Any]], index: int) -> int:
    idx = max(0, min(index, len(messages)))
    while idx > 0 and idx < len(messages) and not _is_history_boundary(messages[idx]):
        idx -= 1
    return idx


def _safe_filename(key: str) -> str:
    return re.sub(r"[^\w\-]", "_", key)


@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0
    consolidation_requested: bool = False

    def add_message(self, role: str, content: str, media: list[str] | None = None, **kwargs: Any) -> None:
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().astimezone().isoformat(),
            **kwargs,
        }
        if media:
            msg["media"] = list(media)
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 40,
        *,
        start_index: int | None = None,
    ) -> list[dict[str, Any]]:
        if start_index is not None:
            if max_messages <= 0:
                return []
            start = max(0, int(start_index))
            if start >= len(self.messages):
                return []
            while (
                start > 0
                and self.messages[start].get("role") != "user"
                and not (self.messages[start].get("role") == "assistant" and self.messages[start].get("proactive"))
            ):
                start -= 1
            messages = self.messages[start:]
            if messages and not (
                messages[0].get("role") == "user"
                or (messages[0].get("role") == "assistant" and messages[0].get("proactive"))
            ):
                messages = _align_to_user_boundary(messages)
            if not messages:
                return []
            # 限制返回的消息数量
            messages = messages[-max_messages:]
        elif max_messages <= 0:
            messages = []
        else:
            messages = self.messages[-max_messages:]
        # 每轮 user 消息都原样回放当时的 llm_context_frame。这看似冗余，但为 DeepSeek 的
        # prefix prompt cache 提供了稳定前缀：当前轮 context_frame 与历史 context_frame
        # 前缀对齐，cache_write 只需支付最新差异部分。
        out: list[dict[str, Any]] = []

        # 最后一条 assistant 消息视为“当前轮”，其 tool result 保留完整预算；
        # 更早的 assistant 消息都是历史轮，tool result 激进截断以控制 input 增长。
        last_assistant_index = -1
        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and not m.get("proactive"):
                last_assistant_index = i

        for i, m in enumerate(messages):
            role = m.get("role")

            if role == "user":
                context_frame = m.get("llm_context_frame")
                if isinstance(context_frame, str) and context_frame.strip():
                    out.append({"role": "user", "content": context_frame})
                user_content = m.get("llm_user_content")
                if user_content is None:
                    text = m.get("content", "")
                    media_paths = m.get("media") or []
                    user_content = _rebuild_user_content(text, media_paths) if media_paths else text
                out.append({"role": "user", "content": user_content})
                continue

            if role != "assistant":
                continue

            content = m.get("content", "") or ""
            if m.get("proactive"):
                out.extend(_build_proactive_history_messages(str(content), m))
                continue

            # 历史轮 tool result 用更小预算，当前轮保留完整
            is_current_assistant = i == last_assistant_index
            tool_budget = _TOOL_RESULT_CHAR_BUDGET if is_current_assistant else _TOOL_RESULT_HISTORY_CHAR_BUDGET

            tool_chain: list[dict] = m.get("tool_chain") or []
            for group in tool_chain:
                calls: list[dict] = group.get("calls") or []
                if not calls:
                    continue
                assistant_msg = {
                    "role": "assistant",
                    "content": group.get("text"),
                    "tool_calls": [
                        {
                            "id": c["call_id"],
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False),
                            },
                        }
                        for c in calls
                    ],
                }
                reasoning_content = group.get("reasoning_content")
                if isinstance(reasoning_content, str):
                    assistant_msg["reasoning_content"] = reasoning_content
                out.append(assistant_msg)
                for c in calls:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": c["call_id"],
                            "content": _truncate_tool_result(c.get("result", ""), budget=tool_budget),
                        }
                    )

            if content:
                content = _append_proactive_meta(content, m)
            # 历史轮 assistant 回复截断，控制 input 增长；当前轮保留完整
            if not is_current_assistant and len(content) > _ASSISTANT_HISTORY_CHAR_BUDGET:
                content = content[:_ASSISTANT_HISTORY_CHAR_BUDGET] + "\n\n…（历史回复已截断）"
            assistant_msg = {"role": "assistant", "content": content}
            reasoning_content = m.get("reasoning_content")
            if isinstance(reasoning_content, str):
                assistant_msg["reasoning_content"] = reasoning_content
            out.append(assistant_msg)

        return out

    def clear(self) -> None:
        self.messages = []
        self.updated_at = datetime.now()
        self.last_consolidated = 0
        self.consolidation_requested = False


class SessionManager:
    _METADATA_REFRESH_EVERY: int = 10

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.session_dir = workspace / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = workspace / "sessions.db"
        self._store = SessionStore(self.db_path)
        self._cache: dict[str, Session] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._write_locks:
            self._write_locks[key] = asyncio.Lock()
        return self._write_locks[key]

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key)
            self._ensure_session_meta(session)
        self._cache[key] = session
        return session

    def peek_next_message_id(self, session_key: str) -> str:
        next_seq = self._store.next_seq(session_key)
        return f"{session_key}:{next_seq}"

    def _load(self, key: str) -> Session | None:
        meta = self._store.get_session_meta(key)
        messages = self._store.fetch_session_messages(key)
        if meta is None and not messages:
            return None

        created_at = datetime.fromisoformat(meta["created_at"]) if meta and meta.get("created_at") else datetime.now()
        updated_at = datetime.fromisoformat(meta["updated_at"]) if meta and meta.get("updated_at") else datetime.now()
        metadata = meta.get("metadata", {}) if meta else {}
        last_consolidated = int(meta.get("last_consolidated", 0)) if meta else 0
        return Session(
            key=key,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
            last_consolidated=last_consolidated,
        )

    def _ensure_session_meta(self, session: Session) -> None:
        self._store.upsert_session(
            session.key,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            last_consolidated=session.last_consolidated,
            metadata=session.metadata,
        )

    def _extract_extra(self, msg: dict[str, Any]) -> dict[str, Any]:
        skip = {
            "id",
            "session_key",
            "seq",
            "role",
            "content",
            "timestamp",
            "tool_chain",
        }
        return {k: v for k, v in msg.items() if k not in skip}

    def _persist_messages(self, session: Session, messages: list[dict[str, Any]]) -> int:
        next_seq = self._store.next_seq(session.key)
        inserted = 0

        for msg in messages:
            if msg.get("id"):
                continue
            ts = str(msg.get("timestamp") or datetime.now().astimezone().isoformat())
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            row = self._store.insert_message(
                session.key,
                role=str(msg.get("role") or "assistant"),
                content=content,
                ts=ts,
                seq=next_seq,
                tool_chain=msg.get("tool_chain"),
                extra=self._extract_extra(msg),
            )
            msg.update(row)
            next_seq += 1
            inserted += 1

        for msg in messages:
            if "timestamp" not in msg:
                msg["timestamp"] = datetime.now().astimezone().isoformat()

        return inserted

    def save(self, session: Session) -> None:
        session.updated_at = datetime.now()
        self._ensure_session_meta(session)
        self._persist_messages(session, session.messages)
        self._store.upsert_session(
            session.key,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            last_consolidated=session.last_consolidated,
            metadata=session.metadata,
        )
        self._cache[session.key] = session

    async def save_async(self, session: Session) -> None:
        session.updated_at = datetime.now()
        async with self._lock(session.key):
            self.save(session)

    async def append_messages(self, session: Session, messages: list[dict]) -> None:
        session.updated_at = datetime.now()
        msgs_copy = list(messages)
        async with self._lock(session.key):
            self._ensure_session_meta(session)
            self._persist_messages(session, msgs_copy)
            self._store.upsert_session(
                session.key,
                created_at=session.created_at.isoformat(),
                updated_at=session.updated_at.isoformat(),
                last_consolidated=session.last_consolidated,
                metadata=session.metadata,
            )
            self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def delete_session(self, key: str, *, cascade: bool = False) -> bool:
        """删除指定 session，可选择是否级联删除关联数据。"""
        self._cache.pop(key, None)
        return self._store.delete_session(key, cascade=cascade)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = self._store.list_sessions()
        for item in sessions:
            item["path"] = str(self.db_path)
        return sessions

    def prune_for_context_cache(
        self,
        session: Session,
        *,
        keep_messages: int = _CONTEXT_CACHE_KEEP_MESSAGES,
        trigger_messages: int = _CONTEXT_CACHE_PRUNE_TRIGGER,
    ) -> int:
        """Drop old session replay rows so the LLM prompt keeps a stable prefix.

        The canonical chat history remains in the main Conversation/Message tables.
        This only trims the session replay store used to rebuild model prompts.
        """

        keep = max(1, int(keep_messages))
        trigger = max(keep + 1, int(trigger_messages))
        if len(session.messages) <= trigger:
            return 0

        cutoff = _move_back_to_history_boundary(session.messages, len(session.messages) - keep)
        if cutoff <= 0:
            return 0

        removed = session.messages[:cutoff]
        ids = [str(m.get("id")) for m in removed if str(m.get("id") or "").strip()]
        if len(ids) != len(removed):
            # Unpersisted in-memory messages mean save() has not run yet. Do not
            # partially trim because DB and cache would diverge.
            return 0

        remaining = session.messages[cutoff:]
        deleted = self._store.delete_session_messages_and_update_cursor(
            session.key,
            ids=ids,
            last_consolidated=0,
        )
        if deleted != len(ids):
            self.invalidate(session.key)
            return 0

        session.messages = remaining
        session.last_consolidated = 0
        session.updated_at = datetime.now()
        self._cache[session.key] = session
        return deleted

    def get_channel_metadata(self, channel: str) -> list[dict[str, Any]]:
        try:
            return self._store.get_channel_metadata(channel)
        except Exception as e:
            logging.warning("Failed to read channel metadata for %s: %s", channel, e)
            return []


_manager: SessionManager | None = None


def init_session_manager(workspace: Path | None = None) -> SessionManager:
    global _manager
    from core.config import USER_DATA_DIR

    ws = workspace or USER_DATA_DIR
    _manager = SessionManager(ws)
    return _manager


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        return init_session_manager()
    return _manager
