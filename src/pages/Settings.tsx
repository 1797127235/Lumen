import { useEffect, useRef, useState } from "react";
import { useProvidersStore } from "../store/providersStore";

import {
  getConfig,
  getMemoryStats,
  resetMemory,
  getMemoryList,
  deleteMemory,
  reviewMemory,
  updateMemory,
} from "../lib/api";
import { ProvidersTab } from "../components/providers/ProvidersTab";
import type {
  Config,
  MemoryStats,
  MemoryItem,
} from "../lib/api";

type TabKey = "memory" | "providers" | "about";

const TABS: { key: TabKey; label: string; icon: React.ReactNode }[] = [
  {
    key: "providers",
    label: "供应商",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
      </svg>
    ),
  },
  {
    key: "memory",
    label: "记忆",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 001.5-.189m-1.5.189a6.01 6.01 0 01-1.5-.189m3.75 7.478a12.06 12.06 0 01-4.5 0m3.75 2.311a14.981 14.981 0 01-3 0M3 15a8 8 0 1118 0m-9 5.25h.008v.008H12v-.008z" />
      </svg>
    ),
  },
  {
    key: "about",
    label: "关于",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
      </svg>
    ),
  },
];

interface SettingsProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function Settings({ isOpen, onClose }: SettingsProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("providers");
  const [, setConfig] = useState<Config | null>(null);
  const [, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [memStats, setMemStats] = useState<MemoryStats | null>(null);
  const [memResetting, setMemResetting] = useState(false);
  const [memConfirming, setMemConfirming] = useState(false);
  const [memResetMsg, setMemResetMsg] = useState("");

  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memLoading, setMemLoading] = useState(false);
  const [memError, setMemError] = useState("");
  const [memDeleting, setMemDeleting] = useState<string | null>(null);
  const [memConfirmDelete, setMemConfirmDelete] = useState<string | null>(null);
  const [memEditingId, setMemEditingId] = useState<string | null>(null);
  const [memEditText, setMemEditText] = useState("");
  const [memSaving, setMemSaving] = useState(false);
  const [memReviewing, setMemReviewing] = useState<string | null>(null);

  const [docIndexProvider, setDocIndexProvider] = useState("lancedb");
  const [docIndexProviderStatus, setDocIndexProviderStatus] = useState("ready");

  const savedTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError("");
    Promise.all([
      getConfig().catch((e) => ({ _error: `配置: ${e instanceof Error ? e.message : String(e)}` })),
      getMemoryStats().catch(() => ({ status: "error", count: 0 })),
      loadMemories(),
    ])
      .then(([cfg, mem]) => {
        if ('_error' in cfg) {
          setError(cfg._error as string);
        } else {
          setConfig(cfg);
          setDocIndexProvider(cfg.document_index_provider || "lancedb");
          setDocIndexProviderStatus(cfg.document_index_provider_status || "ready");
          setMemStats(mem);
        }
      })
      .catch((e) => setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setLoading(false));
  }, [isOpen]);

  useEffect(() => {
    return () => {
      if (savedTimer.current) window.clearTimeout(savedTimer.current);
    };
  }, []);

  async function loadMemories() {
    setMemError("");
    setMemLoading(true);
    try {
      const items = await getMemoryList();
      setMemories(items);
    } catch {
      setMemError("记忆加载失败");
    } finally {
      setMemLoading(false);
    }
  }

  async function handleMemDelete(id: string) {
    setMemDeleting(id);
    setMemError("");
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
      setMemStats((prev) => (prev ? { ...prev, count: prev.count - 1 } : null));
    } catch {
      setMemError("删除失败");
    } finally {
      setMemDeleting(null);
      setMemConfirmDelete(null);
    }
  }

  async function handleMemReview(id: string, status: "confirmed" | "rejected") {
    setMemReviewing(id);
    setMemError("");
    try {
      await reviewMemory(id, status);
      setMemories((prev) =>
        prev.map((m) => (m.id === id ? { ...m, confirmation_status: status } : m)),
      );
    } catch {
      setMemError("审核失败");
    } finally {
      setMemReviewing(null);
    }
  }

  async function handleMemSaveEdit(id: string) {
    if (!memEditText.trim()) return;
    setMemSaving(true);
    setMemError("");
    try {
      await updateMemory(id, memEditText.trim());
      setMemories((prev) =>
        prev.map((m) =>
          m.id === id
            ? { ...m, memory: memEditText.trim(), confirmation_status: "modified" }
            : m,
        ),
      );
      setMemEditingId(null);
      setMemEditText("");
    } catch {
      setMemError("保存失败");
    } finally {
      setMemSaving(false);
    }
  }

  function startMemEdit(item: MemoryItem) {
    try {
      const parsed = JSON.parse(item.memory);
      setMemEditText(typeof parsed.value === "string" ? parsed.value : item.memory);
    } catch {
      setMemEditText(item.memory);
    }
    setMemEditingId(item.id);
  }

  function confirmationBadge(status: string): { text: string; className: string } {
    if (status === "rejected")
      return { text: "已否认", className: "bg-danger/10 text-danger" };
    if (status === "modified")
      return { text: "已编辑", className: "bg-ink/10 text-ink" };
    return { text: "已确认", className: "bg-success/10 text-success" };
  }

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

  const handleMemReset = async () => {
    if (memResetting) return;
    setMemResetting(true);
    setMemResetMsg("");
    try {
      const res = await resetMemory();
      setMemStats((prev) => (prev ? { ...prev, count: 0 } : null));
      setMemResetMsg(`已清空 ${res.deleted} 条记忆`);
      setMemConfirming(false);
    } catch {
      setMemResetMsg("清空失败，请查看日志");
    } finally {
      setMemResetting(false);
    }
  };

  const TAB_TITLES: Record<TabKey, string> = {
    providers: "供应商",
    memory: "记忆",
    about: "关于",
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="absolute inset-0 bg-bg/70 backdrop-blur-sm" />

      <div className="relative w-[1000px] h-[720px] max-w-[94vw] max-h-[92vh] rounded-sm border border-border-soft bg-surface shadow-2xl flex overflow-hidden">

        {/* 左侧导航 */}
        <aside className="w-[200px] flex-shrink-0 border-r border-border-soft bg-bg/40 flex flex-col py-6">
          {/* 标题行 */}
          <div className="px-5 mb-6 flex items-center gap-3">
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-sm text-text-subtle hover:text-text hover:bg-surface-elevated transition-colors duration-150 cursor-pointer"
              title="关闭"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
              </svg>
            </button>
            <span className="text-base font-semibold text-text">设置</span>
          </div>

          {/* 导航列表 */}
          <nav className="flex flex-col px-2 gap-0.5">
            {TABS.map((tab) => {
              const active = activeTab === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`group relative flex items-center gap-3 px-3 py-[10px] rounded-sm text-sm text-left transition-all duration-150 cursor-pointer ${
                    active
                      ? "bg-surface-elevated text-ink"
                      : "text-text-muted hover:text-text hover:bg-surface-elevated/40"
                  }`}
                >
                  {active && (
                    <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 rounded-full bg-ink" />
                  )}
                  <span className={active ? "text-ink" : "text-text-subtle group-hover:text-text-muted transition-colors"}>
                    {tab.icon}
                  </span>
                  <span className="font-medium">{tab.label}</span>
                </button>
              );
            })}
          </nav>
        </aside>

        {/* 右侧内容 */}
        <main className="flex-1 overflow-y-auto">
          <div className="px-8 py-6 w-full">
            {/* 页面标题 */}
            <h1 className="text-xl font-semibold text-text mb-6">{TAB_TITLES[activeTab]}</h1>

            {/* 错误提示 */}
            {error && (
              <div className="mb-6 flex items-center gap-2 px-4 py-2.5 bg-danger/10 border border-danger/20 text-danger text-sm rounded-sm">
                <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                {error}
              </div>
            )}

            {/* ── 供应商 ── */}
            {activeTab === "providers" && (
              <ProvidersTab />
            )}

            {/* ── 记忆 ── */}
            {activeTab === "memory" && (
              <div className="space-y-6">
                {/* 记忆状态概览 */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="p-4 rounded-sm border border-border bg-surface-elevated">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className={`h-2 w-2 rounded-full ${memStats?.status === "ready" ? "bg-success" : "bg-text-subtle"}`} />
                      <span className="text-sm font-medium text-ink">记忆状态</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {memStats?.status === "ready" ? "就绪" : memStats?.status === "no_api_key" ? "未配置 Key" : memStats?.status === "error" ? "异常" : "初始化中"}
                    </div>
                  </div>
                  <div className="p-4 rounded-sm border border-border bg-surface-elevated">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-sm font-medium text-ink">已记忆</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {memStats?.count ?? "—"} 条
                    </div>
                  </div>
                </div>

                {/* 记忆管理 */}
                <section className="p-6 rounded-sm border border-border bg-surface">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-sm font-medium text-ink">记忆管理</span>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={loadMemories}
                        disabled={memLoading}
                        className="px-3 py-1.5 border border-border rounded-sm text-xs text-text-muted hover:text-text hover:bg-surface-elevated transition-colors disabled:opacity-40"
                      >
                        {memLoading ? "加载中..." : "刷新"}
                      </button>
                      {!memConfirming ? (
                        <button
                          onClick={() => {
                            setMemConfirming(true);
                            setMemResetMsg("");
                          }}
                          disabled={!memStats || memStats.count === 0}
                          className="px-3 py-1.5 border border-border rounded-sm text-xs text-text-muted hover:text-danger hover:border-danger/30 transition-colors disabled:opacity-40"
                        >
                          清空记忆
                        </button>
                      ) : (
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-text-subtle">确认清空？</span>
                          <button
                            onClick={handleMemReset}
                            disabled={memResetting}
                            className="px-3 py-1.5 bg-danger/80 text-bg rounded-sm text-xs disabled:opacity-40"
                          >
                            {memResetting ? "..." : "清空"}
                          </button>
                          <button
                            onClick={() => setMemConfirming(false)}
                            className="px-3 py-1.5 border border-border rounded-sm text-xs text-text"
                          >
                            取消
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  {memResetMsg && (
                    <p
                      className={`mb-4 text-sm ${memResetMsg.includes("失败") ? "text-danger" : "text-success"}`}
                    >
                      {memResetMsg}
                    </p>
                  )}

                  {memError && (
                    <p className="mb-4 text-sm text-danger">{memError}</p>
                  )}

                  {memLoading && memories.length === 0 && (
                    <p className="text-sm text-text-muted py-4">加载中...</p>
                  )}

                  {!memLoading && memories.length === 0 && (
                    <p className="text-sm text-text-muted py-4">
                      还没有记忆。聊几句之后，系统会逐步提取长期记忆。
                    </p>
                  )}

                  {memories.length > 0 && (
                    <div className="space-y-1.5 max-h-[340px] overflow-y-auto scroll-auto-hide pr-1">
                      {memories.map((mem) => {
                        const badge = confirmationBadge(mem.confirmation_status);
                        const isRejected = mem.confirmation_status === "rejected";
                        return (
                          <div
                            key={mem.id}
                            className={`p-3 rounded-sm border ${
                              isRejected
                                ? "border-border-soft bg-surface/50 opacity-70"
                                : "border-border-soft"
                            }`}
                          >
                            {memEditingId === mem.id ? (
                              <div className="flex flex-col gap-2">
                                <textarea
                                  value={memEditText}
                                  onChange={(e) => setMemEditText(e.target.value)}
                                  rows={3}
                                  className="w-full rounded border border-border bg-surface px-3 py-1.5 text-sm text-text outline-none focus:border-ink"
                                />
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => handleMemSaveEdit(mem.id)}
                                    disabled={memSaving}
                                    className="rounded bg-ink px-3 py-1 text-xs text-bg hover:bg-ink/80 disabled:opacity-50"
                                  >
                                    {memSaving ? "保存中..." : "保存"}
                                  </button>
                                  <button
                                    onClick={() => {
                                      setMemEditingId(null);
                                      setMemEditText("");
                                    }}
                                    className="rounded border border-border px-3 py-1 text-xs text-text-subtle hover:bg-surface"
                                  >
                                    取消
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <>
                                <p
                                  className={`text-sm leading-relaxed ${
                                    isRejected ? "text-text-muted line-through" : "text-text"
                                  }`}
                                >
                                  {mem.memory}
                                </p>
                                <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex items-center gap-2">
                                    <span className="text-xs text-text-subtle">
                                      {formatDate(mem.created_at)}
                                    </span>
                                    <span
                                      className={`rounded px-1.5 py-0.5 text-xs ${badge.className}`}
                                    >
                                      {badge.text}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    {isRejected ? (
                                      <button
                                        onClick={() => handleMemReview(mem.id, "confirmed")}
                                        disabled={memReviewing === mem.id}
                                        className="text-xs text-success hover:underline disabled:opacity-50"
                                      >
                                        {memReviewing === mem.id ? "..." : "恢复"}
                                      </button>
                                    ) : (
                                      <button
                                        onClick={() => handleMemReview(mem.id, "rejected")}
                                        disabled={memReviewing === mem.id}
                                        className="text-xs text-text-subtle hover:text-danger disabled:opacity-50"
                                      >
                                        {memReviewing === mem.id ? "..." : "否认"}
                                      </button>
                                    )}
                                    <button
                                      onClick={() => startMemEdit(mem)}
                                      className="text-xs text-text-subtle hover:text-ink disabled:opacity-50"
                                    >
                                      编辑
                                    </button>
                                    <button
                                      onClick={() => {
                                        if (memConfirmDelete === mem.id) {
                                          void handleMemDelete(mem.id);
                                        } else {
                                          setMemConfirmDelete(mem.id);
                                        }
                                      }}
                                      disabled={memDeleting === mem.id}
                                      className={`text-xs transition-colors ${
                                        memConfirmDelete === mem.id
                                          ? "text-danger"
                                          : "text-text-subtle hover:text-danger"
                                      }`}
                                    >
                                      {memDeleting === mem.id
                                        ? "删除中..."
                                        : memConfirmDelete === mem.id
                                          ? "确定？"
                                          : "删除"}
                                    </button>
                                  </div>
                                </div>
                              </>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </section>

                {/* 语义搜索 Provider 选择 */}
                <section className="p-6 rounded-sm border border-border bg-surface">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-sm font-medium text-ink">语义搜索</span>
                  </div>

                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs text-text-subtle mb-1.5">Provider</label>
                        <select
                          value={docIndexProvider}
                          onChange={(e) => setDocIndexProvider(e.target.value)}
                          className="w-full px-3 py-2 border border-border rounded-sm text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                        >
                          <option value="lancedb">LanceDB（向量搜索）</option>
                          <option value="disabled">关闭（仅关键词搜索）</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs text-text-subtle mb-1.5">状态</label>
                        <div className="flex items-center gap-2 px-3 py-2">
                          <span
                            className={`h-2 w-2 rounded-full ${
                              docIndexProviderStatus === "ready" ? "bg-success" : "bg-danger"
                            }`}
                          />
                          <span className="text-sm text-text">
                            {docIndexProviderStatus === "ready"
                              ? "就绪"
                              : docIndexProviderStatus === "error"
                              ? "错误"
                              : docIndexProviderStatus === "not_initialized"
                              ? "未初始化"
                              : docIndexProviderStatus}
                          </span>
                        </div>
                      </div>
                    </div>
                    <p className="text-xs text-text-subtle">
                      切换后需重启应用生效
                    </p>
                  </div>
                </section>
              </div>
            )}

            {/* ── 关于 ── */}
            {activeTab === "about" && (
              <div className="space-y-6">
                {/* 应用概览 */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="p-4 rounded-sm border border-border bg-surface-elevated">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="h-2 w-2 rounded-full bg-success" />
                      <span className="text-sm font-medium text-ink">运行状态</span>
                    </div>
                    <div className="text-xs text-text-subtle">本地运行中</div>
                  </div>
                  <div className="p-4 rounded-sm border border-border bg-surface-elevated">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-sm font-medium text-ink">版本</span>
                    </div>
                    <div className="text-xs text-text-subtle">v0.1.0</div>
                  </div>
                </div>

                {/* 关于卡片 */}
                <section className="p-6 rounded-sm border border-border bg-surface">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-sm bg-ink/5 border border-border-soft flex items-center justify-center">
                      <svg className="w-5 h-5 text-ink" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 001.5-.189m-1.5.189a6.01 6.01 0 01-1.5-.189m3.75 7.478a12.06 12.06 0 01-4.5 0m3.75 2.311a14.981 14.981 0 01-3 0M3 15a8 8 0 1118 0m-9 5.25h.008v.008H12v-.008z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-ink">Lumen</h3>
                      <p className="text-xs text-text-subtle">一个真正认识你的 AI 伴侣</p>
                    </div>
                  </div>

                  <div className="space-y-1 mb-4">
                    <p className="text-xs text-text-subtle leading-relaxed">
                      本地运行 · 原生桌面 · 隐私优先
                    </p>
                    <p className="text-xs text-text-subtle leading-relaxed">
                      所有数据存储在本地，不依赖云端，越用越懂你。
                    </p>
                  </div>

                  <div className="pt-4 border-t border-border-soft">
                    <a
                      href="https://github.com/questionliuxinyu/career-os"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-2 text-sm text-text-muted hover:text-ink transition-colors duration-150"
                    >
                      <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
                      </svg>
                      GitHub
                      <svg className="w-3 h-3 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                      </svg>
                    </a>
                  </div>
                </section>
              </div>
            )}

          </div>
        </main>
        {/* Toast */}
        <Toast />
      </div>
    </div>
  );
}

function Toast() {
  const { toastMessage, toastType, toastVisible } = useProvidersStore();
  if (!toastVisible) return null;
  return (
    <div className={`fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-sm shadow-lg text-sm z-50 ${
      toastType === 'error' ? 'bg-danger text-bg' : 'bg-ink text-bg'
    }`}>
      {toastMessage}
    </div>
  );
}
