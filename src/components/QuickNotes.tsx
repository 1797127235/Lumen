import { useEffect, useRef, useState } from "react";
import { listNotes, createNote, updateNote, deleteNote, type Note } from "../lib/api";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH} 小时前`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 7) return `${diffD} 天前`;
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export default function QuickNotes({ isOpen, onClose }: Props) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 打开时加载
  useEffect(() => {
    if (!isOpen) return;
    listNotes()
      .then(setNotes)
      .catch(() => setError("加载失败"));
    // 自动聚焦输入框
    setTimeout(() => textareaRef.current?.focus(), 50);
  }, [isOpen]);

  // ESC 关闭
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  async function handleSave() {
    const text = input.trim();
    if (!text) return;
    setSaving(true);
    setError("");
    try {
      const note = await createNote(text);
      setNotes((prev) => {
        // 去重：相同 ID 移到顶部，不重复添加
        const filtered = prev.filter((n) => n.id !== note.id);
        return [note, ...filtered];
      });
      setInput("");
    } catch {
      setError("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function handleEditSave(id: string) {
    const text = editText.trim();
    if (!text) return;
    try {
      const updated = await updateNote(id, text);
      setNotes((prev) => prev.map((n) => (n.id === id ? updated : n)));
      setEditingId(null);
    } catch {
      setError("编辑失败");
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteNote(id);
      setNotes((prev) => prev.filter((n) => n.id !== id));
    } catch {
      setError("删除失败");
    }
  }

  return (
    /* 背景遮罩 */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-[2px]"
      onClick={onClose}
    >
      {/* 纸张主体 — 阻止冒泡 */}
      <div
        className="relative flex h-[min(84vh,760px)] w-[min(92vw,560px)] flex-shrink-0 flex-col overflow-hidden rounded-[28px] border border-border bg-[linear-gradient(180deg,rgba(38,31,24,0.98)_0%,rgba(31,26,21,0.98)_100%)] shadow-[0_28px_80px_rgba(0,0,0,0.42),inset_0_1px_0_rgba(255,245,214,0.03)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,210,120,0.08),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.015),transparent_30%,transparent_70%,rgba(0,0,0,0.06))]"
        />
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-[radial-gradient(circle_at_top,rgba(243,195,92,0.08),transparent_70%)]" />

        {/* 标题栏 */}
        <div className="relative z-10 flex items-center justify-between border-b border-border-soft px-7 pt-6 pb-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-ink-soft/40">Quick note</p>
            <h2 className="mt-1 text-lg font-semibold text-text">随记</h2>
          </div>
          <button
            onClick={onClose}
            className="cursor-pointer rounded-full p-2 text-text-subtle transition-colors hover:bg-surface-elevated/60 hover:text-text"
            aria-label="关闭随记"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 输入区 */}
        <div className="relative z-10 border-b border-border-soft px-7 pt-5 pb-5">
          <div className="rounded-[24px] border border-[rgba(243,195,92,0.08)] bg-[linear-gradient(180deg,rgba(58,45,33,0.68),rgba(46,37,28,0.92))] px-5 py-5 shadow-[inset_0_1px_0_rgba(255,245,214,0.05),0_14px_36px_rgba(0,0,0,0.16)]">
            <p className="mb-3 text-xs font-medium tracking-[0.14em] text-ink-soft/42">写给 Lumen 的纸条</p>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void handleSave();
              }
            }}
            placeholder="有什么想让 Lumen 知道的？"
            rows={7}
            className="min-h-48 w-full resize-none bg-transparent text-[15px] leading-8.5 text-text placeholder:text-ink-soft/28 outline-none"
          />
          <div className="mt-4 flex items-center justify-between gap-3">
            <span className="text-xs text-text-subtle">Ctrl / ⌘ + Enter 保存</span>
            <button
              onClick={handleSave}
              disabled={saving || !input.trim()}
              className="cursor-pointer rounded-full bg-ink px-4 py-2 text-xs font-semibold text-bg transition-colors hover:bg-ink-deep disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
          </div>
        </div>

        {/* 错误 */}
        {error && (
          <div className="relative z-10 mx-7 mt-3 rounded-2xl bg-danger/10 px-4 py-3 text-xs text-danger">
            {error}
          </div>
        )}

        {/* 记录列表 */}
        <div className="relative z-10 flex-1 overflow-y-auto px-7 py-5 space-y-3">
          {notes.length === 0 && (
            <div className="rounded-[22px] border border-dashed border-[rgba(243,195,92,0.1)] bg-surface-elevated/30 px-5 py-10 text-center">
              <p className="text-sm text-text-muted">还没有记录</p>
              <p className="mt-2 text-xs text-text-subtle">写下一句今天的小事、念头或想法。</p>
            </div>
          )}
          {notes.map((note) => (
            <div key={note.id} className="group">
              {editingId === note.id ? (
                /* 编辑态 */
                <div className="rounded-[20px] border border-[rgba(243,195,92,0.08)] bg-[linear-gradient(180deg,rgba(54,43,31,0.68),rgba(42,34,25,0.92))] px-4 py-4 shadow-[0_10px_24px_rgba(0,0,0,0.14)]">
                  <textarea
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        void handleEditSave(note.id);
                      }
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    autoFocus
                    rows={3}
                    className="w-full resize-none rounded-2xl border border-border-soft bg-black/10 px-3 py-3 text-sm leading-7 text-text outline-none"
                  />
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => void handleEditSave(note.id)}
                      className="cursor-pointer rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-bg hover:bg-ink-deep"
                    >
                      保存
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="cursor-pointer rounded-full px-3 py-1.5 text-xs text-text-subtle hover:bg-surface/50"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                /* 展示态 */
                <div className="flex gap-3 rounded-[22px] border border-[rgba(243,195,92,0.07)] bg-[linear-gradient(180deg,rgba(60,47,33,0.42),rgba(45,36,27,0.82))] px-4 py-4 shadow-[0_10px_25px_rgba(0,0,0,0.12)]">
                  <div className="flex-1">
                    <p className="whitespace-pre-wrap text-sm leading-7 text-text">
                      {note.content}
                    </p>
                    <p className="mt-2 text-xs text-text-subtle">
                      {formatTime(note.updated_at ?? note.created_at)}
                      {note.updated_at && note.updated_at !== note.created_at && " · 已编辑"}
                    </p>
                  </div>
                  {/* hover 操作 */}
                  <div className="flex flex-shrink-0 gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      onClick={() => { setEditingId(note.id); setEditText(note.content); }}
                      className="cursor-pointer rounded-full p-2 text-text-subtle hover:bg-surface/50 hover:text-ink"
                      title="编辑"
                      aria-label="编辑随记"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z" />
                      </svg>
                    </button>
                    <button
                      onClick={() => void handleDelete(note.id)}
                      className="cursor-pointer rounded-full p-2 text-text-subtle hover:bg-danger/10 hover:text-danger"
                      title="删除"
                      aria-label="删除随记"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                      </svg>
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
