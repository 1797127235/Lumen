import { useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  getConfig,
  updateConfig,
  testConfig,
  getMemoryStats,
  resetMemory,
  getProviders,
  listDataSources,
  createDataSource,
  deleteDataSource,
  syncDataSource,
} from "../lib/api";
import type {
  Config,
  ConfigTestResponse,
  MemoryStats,
  ProviderCatalog,
  DataSource,
} from "../lib/api";

type TabKey = "ai" | "memory" | "knowledge" | "about";

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
    key: "knowledge",
    label: "知识库",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
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

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-xs mb-md">
      <span className="w-0.5 h-4 rounded-full bg-ink opacity-70 flex-shrink-0" />
      <h2 className="text-sm font-medium text-text-muted tracking-wide uppercase">{children}</h2>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2xs">
      <label className="block text-xs text-text-subtle">{label}</label>
      {children}
    </div>
  );
}

const inputCls =
  "w-full px-sm py-[9px] border border-border rounded-lg text-sm bg-surface-elevated text-text outline-none focus:border-ink transition-colors duration-150 placeholder:text-text-subtle";

const selectCls =
  "w-full px-sm py-[9px] border border-border rounded-lg text-sm bg-surface-elevated text-text outline-none focus:border-ink transition-colors duration-150 cursor-pointer";

export default function Settings({ isOpen, onClose }: SettingsProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("ai");
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
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

  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [dsLoading, setDsLoading] = useState(false);
  const [addingFolder, setAddingFolder] = useState(false);

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
      loadDataSources(),
    ])
      .then(([cfg, providers, mem]) => {
        setConfig(cfg);
        setLlmProvider(cfg.llm_provider || "dashscope");
        setLlmModel(cfg.llm_model || "qwen-plus");
        setLlmBaseUrl(cfg.llm_base_url || "");
        setEmbeddingProvider(cfg.embedding_provider || "dashscope");
        setEmbeddingModel(cfg.embedding_model || "text-embedding-v4");
        setEmbeddingBaseUrl(cfg.embedding_base_url || "");
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

  async function loadDataSources() {
    setDsLoading(true);
    try {
      const sources = await listDataSources();
      setDataSources(sources);
    } catch (err) {
      console.error("[加载数据源失败]", err);
      setError(err instanceof Error ? err.message : "加载数据源失败");
    } finally {
      setDsLoading(false);
    }
  }

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

  const handleBrowseFolder = async () => {
    setError("");
    try {
      const selected = await open({ directory: true, multiple: false });
      // Tauri v2 dialog: string | string[] | null
      const path = Array.isArray(selected) ? selected[0] : selected;
      if (!path || typeof path !== "string") return;
      setAddingFolder(true);
      const name = path.split(/[\\/]/).pop() || "未命名";
      const created = await createDataSource({
        name,
        type: "local_folder",
        config: { paths: [path] },
      });
      await syncDataSource(created.id);
      await loadDataSources();
    } catch (err) {
      console.error("[添加数据源失败]", err);
      setError(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAddingFolder(false);
    }
  };

  const handleRemoveDataSource = async (id: string) => {
    if (!confirm("确定移除此资料来源？Lumen 会忘记已阅读的内容。")) return;
    try {
      await deleteDataSource(id);
      await loadDataSources();
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除失败");
    }
  };

  const handleSyncDataSource = async (id: string) => {
    try {
      await syncDataSource(id);
      await loadDataSources();
    } catch (err) {
      setError(err instanceof Error ? err.message : "同步失败");
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
                  </div>

                  {!memConfirming ? (
                    <button
                      onClick={() => {
                        setMemConfirming(true);
                        setMemResetMsg("");
                      }}
                      disabled={!memStats || memStats.count === 0}
                      className="px-md py-2 border border-border rounded-lg text-sm text-text hover:bg-surface-elevated transition-colors disabled:opacity-40"
                    >
                      清空记忆
                    </button>
                  ) : (
                    <div className="flex items-center gap-sm">
                      <span className="text-sm text-text-subtle">清空后 AI 将忘记所有记录，确认吗？</span>
                      <button
                        onClick={handleMemReset}
                        disabled={memResetting}
                        className="px-md py-2 bg-danger/80 text-bg rounded-lg text-sm disabled:opacity-40"
                      >
                        {memResetting ? "清空中..." : "确认清空"}
                      </button>
                      <button
                        onClick={() => setMemConfirming(false)}
                        className="px-md py-2 border border-border rounded-lg text-sm text-text"
                      >
                        取消
                      </button>
                    </div>
                  )}

                  {memResetMsg && (
                    <p
                      className={`mt-xs text-sm ${memResetMsg.includes("失败") ? "text-danger" : "text-success"}`}
                    >
                      {memResetMsg}
                    </p>
                  )}
                </section>
              </div>
            )}

            {/* ── 知识库 ── */}
            {activeTab === "knowledge" && (
              <div className="space-y-lg">
                {/* 连接概览 */}
                <div className="grid grid-cols-2 gap-sm">
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className={`h-2 w-2 rounded-full ${dataSources.length > 0 ? "bg-success" : "bg-text-subtle"}`} />
                      <span className="text-sm font-medium text-ink">已连接</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {dataSources.length > 0 ? `${dataSources.length} 个数据源` : "未添加"}
                    </div>
                  </div>
                  <div className="p-md rounded-lg border border-border bg-surface-elevated">
                    <div className="flex items-center gap-xs mb-2xs">
                      <span className={`h-2 w-2 rounded-full ${dataSources.some(ds => ds.status === "active") ? "bg-success" : "bg-text-subtle"}`} />
                      <span className="text-sm font-medium text-ink">索引状态</span>
                    </div>
                    <div className="text-xs text-text-subtle">
                      {dataSources.some(ds => ds.status === "active") ? "同步中" : dataSources.length > 0 ? "暂停" : "—"}
                    </div>
                  </div>
                </div>

                {/* 数据源管理 */}
                <section className="p-lg rounded-xl border border-border bg-surface">
                  <div className="flex items-center justify-between mb-md">
                    <span className="text-sm font-medium text-ink">已连接的数据源</span>
                    <button
                      onClick={handleBrowseFolder}
                      disabled={addingFolder}
                      className="inline-flex items-center gap-xs px-sm py-1.5 rounded-lg border border-border text-xs text-text-muted hover:text-text hover:border-border-soft transition-colors disabled:opacity-40 cursor-pointer"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                      </svg>
                      {addingFolder ? "添加中…" : "添加数据源"}
                    </button>
                  </div>

                  <p className="text-xs text-text-subtle mb-md">
                    连接本地文件夹、Web 页面或第三方服务，Lumen 会自动索引其中的内容并学习。
                  </p>

                  {dsLoading ? (
                    <div className="flex items-center gap-sm text-sm text-text-subtle py-md">
                      <div className="w-4 h-4 border-2 border-border border-t-ink rounded-full animate-spin" />
                      加载中…
                    </div>
                  ) : dataSources.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-xl text-center">
                      <svg className="w-8 h-8 text-text-subtle/40 mb-sm" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
                      </svg>
                      <p className="text-sm text-text-subtle">还没有连接任何数据源</p>
                      <p className="text-xs text-text-subtle/60 mt-2xs">支持本地文件夹，更多类型即将推出</p>
                    </div>
                  ) : (
                    <div className="space-y-xs">
                      {dataSources.map((ds) => {
                        const paths = (ds.config as { paths?: string[] }).paths || [];
                        const path = paths[0] || "";
                        const statusMap: Record<string, { color: string; label: string }> = {
                          active: { color: "text-success", label: "同步中" },
                          paused: { color: "text-amber-400", label: "暂停" },
                          error: { color: "text-danger", label: "失败" },
                        };
                        const status = statusMap[ds.status] || { color: "text-text-subtle", label: ds.status };

                        const typeLabelMap: Record<string, string> = {
                          local_folder: "本地文件夹",
                          web_url: "网页",
                          github_repo: "GitHub",
                        };

                        return (
                          <div
                            key={ds.id}
                            className="flex items-center gap-sm p-sm rounded-xl border border-border-soft bg-surface-elevated/30 hover:bg-surface-elevated/60 transition-colors duration-150"
                          >
                            <div className="w-9 h-9 rounded-lg bg-surface-elevated border border-border-soft flex items-center justify-center flex-shrink-0">
                              <svg className="h-4 w-4 text-text-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
                              </svg>
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-xs">
                                <span className="text-sm font-medium text-text truncate">{ds.name}</span>
                                <span className="text-xs text-text-subtle/60 flex-shrink-0">{typeLabelMap[ds.type] || ds.type}</span>
                                <span className={`text-xs flex-shrink-0 ${status.color}`}>{status.label}</span>
                              </div>
                              {path && (
                                <div className="text-xs text-text-subtle truncate mt-0.5">{path}</div>
                              )}
                            </div>
                            <div className="flex items-center gap-0.5 flex-shrink-0">
                              <button
                                onClick={() => handleSyncDataSource(ds.id)}
                                title="重新同步"
                                disabled={ds.status !== "active"}
                                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-subtle hover:text-text hover:bg-surface-elevated transition-colors disabled:opacity-30 cursor-pointer"
                              >
                                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
                                </svg>
                              </button>
                              <button
                                onClick={() => handleRemoveDataSource(ds.id)}
                                title="移除"
                                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-subtle hover:text-danger hover:bg-danger/10 transition-colors cursor-pointer"
                              >
                                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                                </svg>
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
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
