import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  createDataSource,
  deleteDataSource,
  listDataSources,
  syncDataSource,
  type DataSource,
} from "../lib/api";

const TYPE_LABELS: Record<string, string> = {
  local_folder: "本地文件夹",
};

const STATUS_META: Record<
  string,
  { dot: string; label: string; badge: string }
> = {
  active: {
    dot: "bg-success",
    label: "正在学习",
    badge: "border-success/20 bg-success/5 text-success",
  },
  paused: {
    dot: "bg-amber-500",
    label: "暂停中",
    badge: "border-amber-500/20 bg-amber-500/5 text-amber-500",
  },
  error: {
    dot: "bg-danger",
    label: "读取失败",
    badge: "border-danger/20 bg-danger/5 text-danger",
  },
};

function formatTime(iso: string | null): string {
  if (!iso) return "尚未阅读";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "尚未阅读";
  return d.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getFolderPath(ds: DataSource): string {
  const paths = (ds.config as { paths?: string[] }).paths;
  if (paths && paths.length > 0) return paths[0];
  return "";
}

export default function MyWorld() {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [filtered, setFiltered] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [actingId, setActingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!search.trim()) {
      setFiltered(sources);
      return;
    }
    const q = search.toLowerCase();
    setFiltered(
      sources.filter((ds) => {
        const name = ds.name.toLowerCase();
        const path = getFolderPath(ds).toLowerCase();
        return name.includes(q) || path.includes(q);
      })
    );
  }, [search, sources]);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const list = await listDataSources();
      setSources(list);
      setFiltered(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("确定移除此资料来源？Lumen 会忘记已阅读的内容。")) return;
    setActingId(id);
    try {
      await deleteDataSource(id);
      const next = sources.filter((s) => s.id !== id);
      setSources(next);
      setFiltered(search.trim() ? next.filter((ds) => {
        const q = search.toLowerCase();
        return ds.name.toLowerCase().includes(q) || getFolderPath(ds).toLowerCase().includes(q);
      }) : next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除失败");
    } finally {
      setActingId(null);
    }
  }

  async function handleSync(id: string) {
    setActingId(id);
    try {
      await syncDataSource(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "同步失败");
    } finally {
      setActingId(null);
    }
  }

  async function handleAddFolder() {
    setError("");
    try {
      const selected = await open({ directory: true, multiple: false });
      const path = Array.isArray(selected) ? selected[0] : selected;
      if (!path || typeof path !== "string") return;
      setAdding(true);
      const name = path.split(/[\\/]/).pop() || "未命名";
      const created = await createDataSource({
        name,
        type: "local_folder",
        config: { paths: [path] },
      });
      await syncDataSource(created.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="w-full max-w-[42rem] mx-auto px-md py-xl ink-fade-in">
      {/* 标题 */}
      <div className="mb-lg flex items-end justify-between">
        <div>
          <h1 className="text-xl font-han text-ink mb-2xs">我的世界</h1>
          <p className="text-sm text-text-muted">
            Lumen 已阅读的资料，在对话中会自动引用
          </p>
        </div>
        <button
          onClick={handleAddFolder}
          disabled={adding}
          className="inline-flex items-center gap-xs px-sm py-2 rounded-lg border border-border text-xs text-text-muted hover:text-text hover:border-border-soft transition-colors disabled:opacity-40"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          {adding ? "添加中…" : "添加本地文件夹"}
        </button>
      </div>

      {/* 搜索 */}
      <div className="mb-md">
        <div className="relative">
          <svg
            className="absolute left-sm top-1/2 -translate-y-1/2 h-4 w-4 text-text-subtle"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
            />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索资料..."
            className="w-full pl-8 pr-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
          />
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div className="mb-md px-sm py-xs bg-danger/10 text-danger text-sm rounded-lg">
          {error}
        </div>
      )}

      {/* 加载中 */}
      {loading && (
        <div className="flex items-center justify-center py-24 text-sm text-text-muted">
          <svg className="mr-xs h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          加载中…
        </div>
      )}

      {/* 空状态 */}
      {!loading && filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="mb-md p-lg rounded-2xl bg-surface-elevated border border-border-soft">
            <svg
              className="h-10 w-10 text-text-subtle/50"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={1}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"
              />
            </svg>
          </div>
          <h3 className="text-base font-medium text-ink mb-2xs">
            {search.trim() ? "未找到匹配的资料" : "Lumen 还没读过任何资料"}
          </h3>
          <p className="text-sm text-text-muted max-w-[22rem] mb-md leading-relaxed">
            {search.trim()
              ? "试试其他关键词"
              : "点击右上角「添加本地文件夹」选择路径，Lumen 会自动阅读其中的内容"}
          </p>
        </div>
      )}

      {/* 列表 */}
      {!loading && filtered.length > 0 && (
        <div className="space-y-sm">
          {filtered.map((ds) => {
            const meta = STATUS_META[ds.status] || {
              dot: "bg-text-subtle",
              label: ds.status,
              badge: "border-border bg-surface text-text-subtle",
            };
            const isActing = actingId === ds.id;
            const folderPath = getFolderPath(ds);

            return (
              <div
                key={ds.id}
                className="group border border-border rounded-xl p-md bg-surface hover:border-border-soft transition-colors"
              >
                <div className="flex items-start gap-md">
                  <div className="flex-shrink-0 mt-0.5">
                    <div className="w-10 h-10 rounded-lg bg-surface-elevated border border-border-soft flex items-center justify-center">
                      <svg
                        className="h-5 w-5 text-text-subtle/60"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={1.5}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
                        />
                      </svg>
                    </div>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-xs mb-2xs flex-wrap">
                      <span className="text-sm font-medium text-ink">{ds.name}</span>
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs border ${meta.badge}`}
                      >
                        <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                        {meta.label}
                      </span>
                    </div>

                    {folderPath && (
                      <div className="text-xs text-text-subtle truncate mb-2xs">
                        {folderPath}
                      </div>
                    )}

                    <div className="flex items-center gap-xs text-xs text-text-subtle/70">
                      <span>{TYPE_LABELS[ds.type] || ds.type}</span>
                      <span>·</span>
                      <span>最近阅读 {formatTime(ds.last_sync_at)}</span>
                    </div>

                    {ds.last_error && (
                      <div className="mt-xs text-xs text-danger bg-danger/5 px-2 py-1.5 rounded-md border border-danger/10">
                        {ds.last_error}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                  >
                    <button
                      title="重新阅读"
                      disabled={isActing || ds.status !== "active"}
                      onClick={() => handleSync(ds.id)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors disabled:opacity-30 text-text-subtle hover:bg-surface-elevated hover:text-text"
                    >
                      <svg className={`h-4 w-4 ${isActing ? "animate-spin" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
                      </svg>
                    </button>

                    <button
                      title="移除"
                      disabled={isActing}
                      onClick={() => handleDelete(ds.id)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors text-text-subtle hover:bg-danger/10 hover:text-danger disabled:opacity-30"
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
