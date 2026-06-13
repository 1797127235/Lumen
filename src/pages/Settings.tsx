import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useProvidersStore } from "../store/providersStore";

import {
  getConfig,
  getMemoryContent,
  getMemoryStats,
  resetMemory,
  saveMemoryContent,
} from "../lib/api";
import { ProvidersTab } from "../components/providers/ProvidersTab";
import type {
  Config,
  MemoryStats,
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

  const [memContent, setMemContent] = useState("");
  const [memOriginal, setMemOriginal] = useState("");
  const [memContentLoading, setMemContentLoading] = useState(false);
  const [memSaving, setMemSaving] = useState(false);
  const [memSaveMsg, setMemSaveMsg] = useState("");
  const [pathCopyMsg, setPathCopyMsg] = useState("");
  const memHasChanges = memContent !== memOriginal;
  const inTauri = isTauri();

  const openMemoryFolder = async () => {
    if (!memStats?.path) return;
    if (inTauri) {
      try {
        await invoke("open_path", { path: memStats.path });
      } catch (e) {
        setMemSaveMsg(`打开失败：${e instanceof Error ? e.message : String(e)}`);
      }
    } else {
      try {
        await navigator.clipboard.writeText(memStats.path);
        setPathCopyMsg("已复制");
        setTimeout(() => setPathCopyMsg(""), 2000);
      } catch {
        setPathCopyMsg("复制失败");
      }
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError("");
    Promise.all([
      getConfig().catch((e) => ({ _error: `配置: ${e instanceof Error ? e.message : String(e)}` })),
      getMemoryStats().catch(() => ({ status: "error", count: 0, path: "" })),
      getMemoryContent().catch(() => ({ content: "" })),
    ])
      .then(([cfg, mem, memDoc]) => {
        if ('_error' in cfg) {
          setError(cfg._error as string);
        } else {
          setConfig(cfg);
          setMemStats(mem);
          setMemContent(memDoc.content);
          setMemOriginal(memDoc.content);
        }
      })
      .catch((e) => setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setLoading(false));
  }, [isOpen]);

  const loadMemContent = async () => {
    setMemContentLoading(true);
    setMemSaveMsg("");
    try {
      const data = await getMemoryContent();
      setMemContent(data.content);
      setMemOriginal(data.content);
    } catch {
      setMemSaveMsg("记忆读取失败，稍后再试");
    } finally {
      setMemContentLoading(false);
    }
  };

  const handleMemSave = async () => {
    if (memSaving) return;
    setMemSaving(true);
    setMemSaveMsg("");
    try {
      await saveMemoryContent(memContent);
      setMemOriginal(memContent);
      setMemSaveMsg("已保存");
    } catch {
      setMemSaveMsg("保存失败");
    } finally {
      setMemSaving(false);
    }
  };

  const handleMemReset = async () => {
    if (memResetting) return;
    setMemResetting(true);
    setMemResetMsg("");
    try {
      const res = await resetMemory();
      setMemStats((prev) => (prev ? { ...prev, count: 0 } : null));
      setMemContent("");
      setMemOriginal("");
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
            <h1 className="text-xl font-semibold text-text mb-6">{TAB_TITLES[activeTab]}</h1>

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
                      {memStats?.status === "ready" ? "就绪" : memStats?.status === "error" ? "异常" : "初始化中"}
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

                {/* 记忆全文编辑 */}
                <section className="p-6 rounded-sm border border-border bg-surface">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-sm font-medium text-ink">记忆内容</span>
                    <div className="flex items-center gap-2">
                      {memHasChanges && (
                        <span className="text-xs text-amber-600">未保存</span>
                      )}
                      <button
                        onClick={loadMemContent}
                        disabled={memContentLoading}
                        className="px-3 py-1.5 border border-border rounded-sm text-xs text-text hover:bg-surface-elevated disabled:opacity-40 transition-colors"
                      >
                        {memContentLoading ? "加载中..." : "刷新"}
                      </button>
                      <button
                        onClick={handleMemSave}
                        disabled={memSaving || !memHasChanges}
                        className="px-3 py-1.5 bg-ink text-bg rounded-sm text-xs hover:bg-ink-deep disabled:opacity-40 transition-colors"
                      >
                        {memSaving ? "保存中..." : "保存"}
                      </button>
                    </div>
                  </div>

                  {memSaveMsg && (
                    <p className={`mb-3 text-sm ${memSaveMsg.includes("失败") ? "text-danger" : "text-success"}`}>
                      {memSaveMsg}
                    </p>
                  )}

                  <textarea
                    value={memContent}
                    onChange={(e) => setMemContent(e.target.value)}
                    className="w-full min-h-[280px] rounded-sm border border-border-soft bg-surface-elevated px-4 py-3 text-sm text-text leading-relaxed focus:border-ink focus:outline-none resize-none"
                    placeholder="记忆内容为空。多和 Lumen 聊聊，它会逐渐了解你。"
                  />

                  <div className="mt-3 flex items-center justify-between gap-3">
                    <p className="text-xs text-text-subtle truncate" title={memStats?.path || ""}>
                      {memStats?.path
                        ? `存储位置：${memStats.path}`
                        : "记忆以 Markdown 文件形式存储在本地，可直接编辑。"}
                    </p>
                    {memStats?.path && (
                      <button
                        onClick={openMemoryFolder}
                        className="shrink-0 px-3 py-1.5 border border-border rounded-sm text-xs text-text hover:bg-surface-elevated transition-colors"
                      >
                        {inTauri ? "打开文件夹" : pathCopyMsg || "复制路径"}
                      </button>
                    )}
                  </div>
                </section>

                {/* 记忆管理 */}
                <section className="p-6 rounded-sm border border-border bg-surface">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-sm font-medium text-ink">记忆管理</span>

                    <div className="flex items-center gap-2">
                      {!memConfirming ? (
                        <button
                          onClick={() => {
                            setMemConfirming(true);
                            setMemResetMsg("");
                          }}
                          className="px-3 py-1.5 border border-border rounded-sm text-xs text-text-muted hover:text-danger hover:border-danger/30 transition-colors"
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
                      className={`text-sm ${memResetMsg.includes("失败") ? "text-danger" : "text-success"}`}
                    >
                      {memResetMsg}
                    </p>
                  )}

                </section>
              </div>
            )}

            {/* ── 关于 ── */}
            {activeTab === "about" && (
              <div className="space-y-6">
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

                <section className="p-6 rounded-sm border border-border bg-surface">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-sm bg-ink/5 border border-border-soft flex items-center justify-center">
                      <svg className="w-5 h-5 text-ink" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 001.5-.189m-1.5.189a6.01 6.01 0 01-1.5-.189m3.75 7.478a12.06 12.06 0 01-4.5 0m3.75 2.311a14.981 14.981 0 01-3 0M3 15a8 8 0 1118 0m-9 5.25h.008v.008H12v-.008z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-ink">Lumen</h3>
                      <p className="text-xs text-text-subtle">一个真正认识你的 AI 伙伴</p>
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
                      href="https://github.com/1797127235/Lumen"
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
