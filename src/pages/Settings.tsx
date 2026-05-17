import { useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  getConfig,
  updateConfig,
  testConfig,
  getMemoryStats,
  resetMemory,
  getProviders,
  getMemoryList,
  deleteMemory,
  reviewMemory,
  updateMemory,
} from "../lib/api";
import type {
  Config,
  ConfigTestResponse,
  MemoryStats,
  ProviderCatalog,
  MemoryItem,
} from "../lib/api";

type TabKey = "ai" | "memory" | "about";

const TABS: { key: TabKey; label: string; icon: React.ReactNode }[] = [
  {
    key: "ai",
    label: "AI 配置",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
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
  const [activeTab, setActiveTab] = useState<TabKey>("ai");
  const [config, setConfig] = useState<Config | null>(null);
  const [, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [llmProvider, setLlmProvider] = useState("dashscope");
  const [llmModel, setLlmModel] = useState("qwen-plus");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [embeddingProvider, setEmbeddingProvider] = useState("dashscope");
  const [embeddingModel, setEmbeddingModel] = useState("text-embedding-v4");
  const [embeddingApiKey, setEmbeddingApiKey] = useState("");
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState("");

  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalog | null>(null);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConfigTestResponse | null>(null);

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
  const [docIndexProviderOpen, setDocIndexProviderOpen] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError("");
    Promise.all([
      getConfig(),
      getProviders().catch(() => null),
      getMemoryStats().catch(() => ({ status: "error", count: 0 })),
      loadMemories(),
    ])
      .then(([cfg, providers, mem]) => {
        setConfig(cfg);
        setLlmProvider(cfg.llm_provider || "dashscope");
        setLlmModel(cfg.llm_model || "qwen-plus");
        setLlmBaseUrl(cfg.llm_base_url || "");
        setEmbeddingProvider(cfg.embedding_provider || "dashscope");
        setEmbeddingModel(cfg.embedding_model || "text-embedding-v4");
        setEmbeddingBaseUrl(cfg.embedding_base_url || "");
        setDocIndexProvider(cfg.document_index_provider || "lancedb");
        setDocIndexProviderStatus(cfg.document_index_provider_status || "ready");
        if (providers) setProviderCatalog(providers);
        setMemStats(mem);
      })
      .catch(() => setError("配置加载失败，请刷新重试"))
      .finally(() => setLoading(false));
  }, [isOpen]);

  useEffect(() => {
    return () => {
      if (savedTimer.current) window.clearTimeout(savedTimer.current);
    };
  }, []);

  const handleProviderChange = (provider: string, isEmbedding = false) => {
    const pc = providerCatalog?.[provider];
    if (!pc) return;
    if (isEmbedding) {
      setEmbeddingProvider(provider);
      setEmbeddingBaseUrl(pc.baseUrl);
      setEmbeddingModel(pc.embeddingModels[0] || "");
    } else {
      setLlmProvider(provider);
      setLlmBaseUrl(pc.baseUrl);
      setLlmModel(pc.models[0] || "");
    }
  };

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const data: Partial<Config> = {
        llm_provider: llmProvider,
        llm_model: llmModel,
        llm_base_url: llmBaseUrl,
        embedding_provider: embeddingProvider,
        embedding_model: embeddingModel,
        embedding_base_url: embeddingBaseUrl,
        document_index_provider: docIndexProvider,
      };
      if (llmApiKey) data.llm_api_key = llmApiKey.trim();
      if (embeddingApiKey) data.embedding_api_key = embeddingApiKey.trim();
      const updated = await updateConfig(data);
      setConfig(updated);
      setLlmApiKey("");
      setEmbeddingApiKey("");
      setSaved(true);
      savedTimer.current = window.setTimeout(() => setSaved(false), 2000);
    } catch {
      setError("保存失败，请检查网络或稍后重试");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (testing) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testConfig({
        provider: llmProvider,
        model: llmModel,
        api_key: llmApiKey.trim(),
        base_url: llmBaseUrl,
      });
      setTestResult(result);
    } catch {
      setTestResult({ ok: false, latency_ms: 0, error: "请求失败" });
    } finally {
      setTesting(false);
    }
  };

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

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="absolute inset-0 bg-bg/70 backdrop-blur-sm" />

      <div className="relative w-[900px] h-[640px] max-w-[94vw] max-h-[92vh] rounded-2xl border border-border-soft bg-surface shadow-2xl flex overflow-hidden">

        {/* 左侧导航 */}
        <aside className="w-[200px] flex-shrink-0 border-r border-border-soft bg-bg/40 flex flex-col py-lg">
          {/* 标题行 */}
          <div className="px-md mb-lg flex items-center gap-xs">
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-text-subtle hover:text-text hover:bg-surface-elevated transition-colors duration-150 cursor-pointer"
              title="关闭"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
              </svg>
            </button>
            <span className="text-sm font-medium text-text">设置</span>
          </div>

          {/* 导航列表 */}
          <nav className="flex flex-col px-xs gap-0.5">
            {TABS.map((tab) => {
              const active = activeTab === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`group relative flex items-center gap-sm px-sm py-[9px] rounded-lg text-sm text-left transition-all duration-150 cursor-pointer ${
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
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>
        </aside>

        {/* 右侧内容 */}
        <main className="flex-1 overflow-y-auto scroll-auto-hide">
          <div className="px-xl py-lg w-full">

            {/* 错误提示 */}
            {error && (
              <div className="mb-md flex items-center gap-xs px-sm py-xs bg-danger/10 border border-danger/20 text-danger text-sm rounded-lg">
                <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                {error}
              </div>
            )}

            {/* ── AI 配置 ── */}
            {activeTab === "ai" && (
              <div className="space-y-lg">
                {/* Provider 状态概览 */}
                <div className="grid grid-cols-2 gap-sm">
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className={`h-2 w-2 rounded-full ${config?.has_llm_key ? "bg-success" : "bg-text-subtle"}`} />
                      <span className="text-sm font-medium text-ink">对话模型</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {config?.has_llm_key ? `${llmProvider} · ${llmModel}` : "未配置"}
                    </div>
                  </div>
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className={`h-2 w-2 rounded-full ${config?.has_embedding_key || config?.has_llm_key ? "bg-success" : "bg-text-subtle"}`} />
                      <span className="text-sm font-medium text-ink">Embedding</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {config?.has_embedding_key || config?.has_llm_key ? `${embeddingProvider} · ${embeddingModel}` : "未配置"}
                    </div>
                  </div>
                </div>

                {/* 对话模型配置 */}
                <section className="p-lg rounded-xl border border-border bg-surface">
                  <div className="flex items-center justify-between mb-md">
                    <div className="flex items-center gap-xs">
                      <span className="text-sm font-medium text-ink">对话模型</span>
                      {config?.has_llm_key && <span className="text-xs text-success">已配置</span>}
                    </div>
                    <div className="flex items-center gap-sm">
                      <button
                        onClick={handleTest}
                        disabled={testing}
                        className="px-sm py-1 border border-border rounded text-xs text-text hover:bg-surface-elevated transition-colors disabled:opacity-40"
                      >
                        {testing ? "测试中..." : "测试"}
                      </button>
                      {testResult && (
                        <span className={`text-xs ${testResult.ok ? "text-success" : "text-danger"}`}>
                          {testResult.ok ? "✓ 正常" : "✗ 失败"}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="space-y-md">
                    <div className="grid grid-cols-2 gap-sm">
                      <div>
                        <label className="block text-xs text-text-subtle mb-xs">Provider</label>
                        <select
                          value={llmProvider}
                          onChange={(e) => handleProviderChange(e.target.value)}
                          className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                        >
                          {Object.entries(providerCatalog ?? {}).map(([key, val]) => (
                            <option key={key} value={key}>{val.name}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs text-text-subtle mb-xs">模型</label>
                        <input
                          value={llmModel}
                          onChange={(e) => setLlmModel(e.target.value)}
                          placeholder={(providerCatalog ?? {})[llmProvider]?.models[0] || "输入模型名"}
                          className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs text-text-subtle mb-xs">API Key</label>
                      <input
                        type="password"
                        value={llmApiKey}
                        onChange={(e) => setLlmApiKey(e.target.value)}
                        placeholder={config?.has_llm_key ? "已保存，留空则不更新" : "sk-..."}
                        className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-text-subtle mb-xs">Base URL（可选）</label>
                      <input
                        value={llmBaseUrl}
                        onChange={(e) => setLlmBaseUrl(e.target.value)}
                        placeholder={(providerCatalog ?? {})[llmProvider]?.baseUrl || "https://..."}
                        className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                      />
                    </div>
                  </div>
                </section>

                {/* Embedding 配置 */}
                <section className="p-lg rounded-xl border border-border bg-surface">
                  <div className="flex items-center justify-between mb-md">
                    <div className="flex items-center gap-xs">
                      <span className="text-sm font-medium text-ink">Embedding 模型</span>
                      {(config?.has_embedding_key || config?.has_llm_key) && <span className="text-xs text-success">已配置</span>}
                    </div>
                  </div>

                  <div className="space-y-md">
                    <div className="grid grid-cols-2 gap-sm">
                      <div>
                        <label className="block text-xs text-text-subtle mb-xs">Provider</label>
                        <select
                          value={embeddingProvider}
                          onChange={(e) => handleProviderChange(e.target.value, true)}
                          className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                        >
                          {Object.entries(providerCatalog ?? {}).map(([key, val]) => (
                            <option key={key} value={key}>{val.name}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs text-text-subtle mb-xs">模型</label>
                        <input
                          value={embeddingModel}
                          onChange={(e) => setEmbeddingModel(e.target.value)}
                          placeholder={(providerCatalog ?? {})[embeddingProvider]?.embeddingModels[0] || "输入模型名"}
                          className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs text-text-subtle mb-xs">API Key（可选，留空则使用 LLM Key）</label>
                      <input
                        type="password"
                        value={embeddingApiKey}
                        onChange={(e) => setEmbeddingApiKey(e.target.value)}
                        placeholder={config?.has_embedding_key ? "已保存，留空则不更新" : "sk-..."}
                        className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-text-subtle mb-xs">Base URL（可选）</label>
                      <input
                        value={embeddingBaseUrl}
                        onChange={(e) => setEmbeddingBaseUrl(e.target.value)}
                        placeholder={(providerCatalog ?? {})[embeddingProvider]?.baseUrl || "https://..."}
                        className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                      />
                    </div>
                  </div>
                </section>

                {/* 保存 */}
                <div className="flex items-center gap-sm">
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="px-lg py-2 rounded-lg text-sm text-bg bg-ink hover:bg-ink-deep transition-colors disabled:opacity-40"
                  >
                    {saving ? "保存中..." : saved ? "已保存" : "保存配置"}
                  </button>
                  {saved && <span className="text-sm text-success">✓ 配置已保存</span>}
                </div>
              </div>
            )}

            {/* ── 记忆 ── */}
            {activeTab === "memory" && (
              <div className="space-y-lg">
                {/* 记忆状态概览 */}
                <div className="grid grid-cols-2 gap-sm">
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className={`h-2 w-2 rounded-full ${memStats?.status === "ready" ? "bg-success" : "bg-text-subtle"}`} />
                      <span className="text-sm font-medium text-ink">记忆状态</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {memStats?.status === "ready" ? "就绪" : memStats?.status === "no_api_key" ? "未配置 Key" : memStats?.status === "error" ? "异常" : "初始化中"}
                    </div>
                  </div>
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className="text-sm font-medium text-ink">已记忆</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {memStats?.count ?? "—"} 条
                    </div>
                  </div>
                </div>

                {/* 记忆管理 */}
                <section className="p-lg rounded-xl border border-border bg-surface">
                  <div className="flex items-center justify-between mb-md">
                    <span className="text-sm font-medium text-ink">记忆管理</span>

                    <div className="flex items-center gap-sm">
                      <button
                        onClick={loadMemories}
                        disabled={memLoading}
                        className="px-sm py-1.5 border border-border rounded-lg text-xs text-text-muted hover:text-text hover:bg-surface-elevated transition-colors disabled:opacity-40"
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
                          className="px-sm py-1.5 border border-border rounded-lg text-xs text-text-muted hover:text-danger hover:border-danger/30 transition-colors disabled:opacity-40"
                        >
                          清空记忆
                        </button>
                      ) : (
                        <div className="flex items-center gap-xs">
                          <span className="text-xs text-text-subtle">确认清空？</span>
                          <button
                            onClick={handleMemReset}
                            disabled={memResetting}
                            className="px-sm py-1.5 bg-danger/80 text-bg rounded-lg text-xs disabled:opacity-40"
                          >
                            {memResetting ? "..." : "清空"}
                          </button>
                          <button
                            onClick={() => setMemConfirming(false)}
                            className="px-sm py-1.5 border border-border rounded-lg text-xs text-text"
                          >
                            取消
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  {memResetMsg && (
                    <p
                      className={`mb-md text-sm ${memResetMsg.includes("失败") ? "text-danger" : "text-success"}`}
                    >
                      {memResetMsg}
                    </p>
                  )}

                  {memError && (
                    <p className="mb-md text-sm text-danger">{memError}</p>
                  )}

                  {memLoading && memories.length === 0 && (
                    <p className="text-sm text-text-muted py-md">加载中...</p>
                  )}

                  {!memLoading && memories.length === 0 && (
                    <p className="text-sm text-text-muted py-md">
                      还没有记忆。聊几句之后，系统会逐步提取长期记忆。
                    </p>
                  )}

                  {memories.length > 0 && (
                    <div className="space-y-xs max-h-[320px] overflow-y-auto scroll-auto-hide pr-1">
                      {memories.map((mem) => {
                        const badge = confirmationBadge(mem.confirmation_status);
                        const isRejected = mem.confirmation_status === "rejected";
                        return (
                          <div
                            key={mem.id}
                            className={`p-sm rounded-lg border ${
                              isRejected
                                ? "border-border-soft bg-surface/50 opacity-70"
                                : "border-border-soft"
                            }`}
                          >
                            {memEditingId === mem.id ? (
                              <div className="flex flex-col gap-xs">
                                <textarea
                                  value={memEditText}
                                  onChange={(e) => setMemEditText(e.target.value)}
                                  rows={3}
                                  className="w-full rounded border border-border bg-surface px-sm py-1 text-sm text-text outline-none focus:border-ink"
                                />
                                <div className="flex gap-xs">
                                  <button
                                    onClick={() => handleMemSaveEdit(mem.id)}
                                    disabled={memSaving}
                                    className="rounded bg-ink px-sm py-1 text-xs text-bg hover:bg-ink/80 disabled:opacity-50"
                                  >
                                    {memSaving ? "保存中..." : "保存"}
                                  </button>
                                  <button
                                    onClick={() => {
                                      setMemEditingId(null);
                                      setMemEditText("");
                                    }}
                                    className="rounded border border-border px-sm py-1 text-xs text-text-subtle hover:bg-surface"
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
                                <div className="mt-2xs flex flex-wrap items-center justify-between gap-sm">
                                  <div className="flex items-center gap-sm">
                                    <span className="text-xs text-text-subtle">
                                      {formatDate(mem.created_at)}
                                    </span>
                                    <span
                                      className={`rounded px-1.5 py-0.5 text-xs ${badge.className}`}
                                    >
                                      {badge.text}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-sm">
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
                <section className="p-lg rounded-xl border border-border bg-surface">
                  <div className="flex items-center justify-between mb-md">
                    <span className="text-sm font-medium text-ink">语义搜索</span>
                  </div>

                  <div className="space-y-md">
                    <div className="grid grid-cols-2 gap-sm">
                      <div>
                        <label className="block text-xs text-text-subtle mb-xs">Provider</label>
                        <select
                          value={docIndexProvider}
                          onChange={(e) => setDocIndexProvider(e.target.value)}
                          className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
                        >
                          <option value="lancedb">LanceDB（向量搜索）</option>
                          <option value="disabled">关闭（仅关键词搜索）</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs text-text-subtle mb-xs">状态</label>
                        <div className="flex items-center gap-xs px-sm py-2">
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
              <div className="space-y-lg">
                {/* 应用概览 */}
                <div className="grid grid-cols-2 gap-sm">
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className="h-2 w-2 rounded-full bg-success" />
                      <span className="text-sm font-medium text-ink">运行状态</span>
                    </div>
                    <div className="text-xs text-text-subtle">本地运行中</div>
                  </div>
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className="text-sm font-medium text-ink">版本</span>
                    </div>
                    <div className="text-xs text-text-subtle">v0.1.0</div>
                  </div>
                </div>

                {/* 关于卡片 */}
                <section className="p-lg rounded-xl border border-border bg-surface">
                  <div className="flex items-center gap-sm mb-md">
                    <div className="w-10 h-10 rounded-xl bg-ink/5 border border-border-soft flex items-center justify-center">
                      <svg className="w-5 h-5 text-ink" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 001.5-.189m-1.5.189a6.01 6.01 0 01-1.5-.189m3.75 7.478a12.06 12.06 0 01-4.5 0m3.75 2.311a14.981 14.981 0 01-3 0M3 15a8 8 0 1118 0m-9 5.25h.008v.008H12v-.008z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-ink">Lumen</h3>
                      <p className="text-xs text-text-subtle">一个真正认识你的 AI 伴侣</p>
                    </div>
                  </div>

                  <div className="space-y-xs mb-md">
                    <p className="text-xs text-text-subtle leading-relaxed">
                      本地运行 · 原生桌面 · 隐私优先
                    </p>
                    <p className="text-xs text-text-subtle leading-relaxed">
                      所有数据存储在本地，不依赖云端，越用越懂你。
                    </p>
                  </div>

                  <div className="pt-md border-t border-border-soft">
                    <a
                      href="https://github.com/questionliuxinyu/career-os"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-xs text-sm text-text-muted hover:text-ink transition-colors duration-150"
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
      </div>
    </div>
  );
}
