import { useEffect, useState } from "react";
import { getMemoryList, getMemoryStats } from "../lib/api";
import type { MemoryItem, MemoryStats } from "../lib/api";

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusLabel(stats: MemoryStats | null): string {
  if (!stats) return "加载中";
  if (stats.status === "ready") return "就绪";
  if (stats.status === "no_api_key") return "未配置 Key";
  if (stats.status === "error") return "异常";
  return "未初始化";
}

export default function Memories() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadMemories() {
    setError("");
    try {
      const [nextStats, nextMemories] = await Promise.all([
        getMemoryStats(),
        getMemoryList(),
      ]);
      setStats(nextStats);
      setMemories(nextMemories);
    } catch {
      setError("记忆读取失败，稍后再试");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadMemories();

    const handleVisible = () => {
      if (!document.hidden) {
        void loadMemories();
      }
    };

    const intervalId = window.setInterval(() => {
      if (!document.hidden) {
        void loadMemories();
      }
    }, 15000);

    window.addEventListener("focus", handleVisible);
    document.addEventListener("visibilitychange", handleVisible);

    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", handleVisible);
      document.removeEventListener("visibilitychange", handleVisible);
    };
  }, []);

  const memoryUnavailable = stats !== null && stats.status !== "ready";

  return (
    <div className="mx-auto max-w-[720px] px-md py-xl">
      <div className="mb-lg flex items-center justify-between gap-md">
        <h1 className="text-xl font-han text-ink">记忆</h1>
        <button
          onClick={() => {
            setLoading(true);
            void loadMemories();
          }}
          className="rounded border border-border px-sm py-1 text-sm text-text hover:bg-surface"
        >
          刷新
        </button>
      </div>

      <div className="mb-md flex items-center gap-sm text-sm text-text-subtle">
        <span>状态：{statusLabel(stats)}</span>
        <span>·</span>
        <span>共 {stats?.count ?? 0} 条</span>
      </div>

      {loading && <p className="text-sm text-text-muted">我在想...</p>}

      {!loading && error && <p className="text-sm text-danger">{error}</p>}

      {!loading && !error && memoryUnavailable && (
        <p className="text-sm text-text-muted">
          记忆服务当前不可用。先去“设置”确认 LLM / Embedding Key 已保存。
          如果你刚保存过配置，现在返回本页或点“刷新”就会重新检查状态。
        </p>
      )}

      {!loading && !error && !memoryUnavailable && memories.length === 0 && (
        <p className="text-text-muted">
          还没有记忆。聊几句之后，系统会逐步提取长期记忆。
        </p>
      )}

      {!loading && !error && !memoryUnavailable && memories.length > 0 && (
        <ul className="flex flex-col gap-xs">
          {memories.map((mem) => (
            <li
              key={mem.id}
              className="rounded-lg border border-border-soft px-md py-sm"
            >
              <p className="leading-relaxed text-text">{mem.memory}</p>
              <p className="mt-2xs text-xs text-text-subtle">
                {formatDate(mem.created_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
