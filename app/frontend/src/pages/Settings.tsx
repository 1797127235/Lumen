import { useEffect, useRef, useState } from "react";
import { getConfig, updateConfig, testConfig, getMemoryStats, resetMemory } from "../lib/api";
import type { Config, ConfigTestResponse, MemoryStats } from "../lib/api";

// ── Provider 配置表（与后端同步）──
const PROVIDER_CONFIG: Record<
  string,
  { name: string; baseUrl: string; models: string[]; embeddingModels: string[] }
> = {
  dashscope: {
    name: "DashScope（阿里云）",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen-plus", "qwen-max", "qwen-turbo"],
    embeddingModels: ["text-embedding-v4"],
  },
  openai: {
    name: "OpenAI",
    baseUrl: "",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    embeddingModels: ["text-embedding-3-small", "text-embedding-3-large"],
  },
  deepseek: {
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"],
    embeddingModels: [],
  },
  anthropic: {
    name: "Anthropic",
    baseUrl: "",
    models: ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
    embeddingModels: [],
  },
  gemini: {
    name: "Gemini（Google）",
    baseUrl: "",
    models: ["gemini-1.5-pro", "gemini-1.5-flash"],
    embeddingModels: ["models/text-embedding-004"],
  },
  ollama: {
    name: "Ollama（本地）",
    baseUrl: "http://localhost:11434",
    models: ["llama3.1", "qwen2.5", "mistral"],
    embeddingModels: ["nomic-embed-text", "mxbai-embed-large"],
  },
  openrouter: {
    name: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    models: ["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "meta-llama/llama-3.1-70b"],
    embeddingModels: [],
  },
  custom: {
    name: "自定义（OpenAI-Compatible）",
    baseUrl: "",
    models: [],
    embeddingModels: [],
  },
};

export default function Settings() {
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const savedTimer = useRef<number | null>(null);

  // LLM 表单
  const [llmProvider, setLlmProvider] = useState("dashscope");
  const [llmModel, setLlmModel] = useState("qwen-plus");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");

  // Embedding 表单
  const [embeddingProvider, setEmbeddingProvider] = useState("dashscope");
  const [embeddingModel, setEmbeddingModel] = useState("text-embedding-v4");
  const [embeddingApiKey, setEmbeddingApiKey] = useState("");
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState("");

  // 测试连接
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConfigTestResponse | null>(null);

  // 记忆管理
  const [memStats, setMemStats] = useState<MemoryStats | null>(null);
  const [memResetting, setMemResetting] = useState(false);
  const [memConfirming, setMemConfirming] = useState(false);
  const [memResetMsg, setMemResetMsg] = useState("");

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        setConfig(cfg);
        setLlmProvider(cfg.llm_provider || "dashscope");
        setLlmModel(cfg.llm_model || "qwen-plus");
        setLlmBaseUrl(cfg.llm_base_url || "");
        setEmbeddingProvider(cfg.embedding_provider || "dashscope");
        setEmbeddingModel(cfg.embedding_model || "text-embedding-v4");
        setEmbeddingBaseUrl(cfg.embedding_base_url || "");
      })
      .catch(() => setError("配置加载失败，请刷新重试"))
      .finally(() => setLoading(false));

    // 同时拉取记忆统计（独立请求，失败不影响配置加载）
    getMemoryStats()
      .then(setMemStats)
      .catch(() => setMemStats({ status: "error", count: 0 }));

    return () => {
      if (savedTimer.current) window.clearTimeout(savedTimer.current);
    };
  }, []);

  // 切换 Provider 时自动填充默认值
  const handleProviderChange = (provider: string, isEmbedding = false) => {
    const pc = PROVIDER_CONFIG[provider];
    if (!pc) return;

    if (isEmbedding) {
      setEmbeddingProvider(provider);
      setEmbeddingBaseUrl(pc.baseUrl);
      if (pc.embeddingModels.length > 0) {
        setEmbeddingModel(pc.embeddingModels[0]);
      } else {
        setEmbeddingModel("");
      }
    } else {
      setLlmProvider(provider);
      setLlmBaseUrl(pc.baseUrl);
      if (pc.models.length > 0) {
        setLlmModel(pc.models[0]);
      } else {
        setLlmModel("");
      }
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
      if (llmApiKey) data.llm_api_key = llmApiKey;
      if (embeddingApiKey) data.embedding_api_key = embeddingApiKey;

      const updated = await updateConfig(data);
      const nextMemStats = await getMemoryStats();
      setConfig(updated);
      setMemStats(nextMemStats);
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
        api_key: llmApiKey.trim(), // 空字符串 → 后端 fallback 到已保存配置
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

  if (loading) return null;

  return (
    <div className="w-full max-w-[32rem] mx-auto px-md py-xl">
      <h1 className="text-xl font-han text-ink mb-md">设置</h1>

      {error && (
        <div className="mb-md px-sm py-xs bg-red/10 text-red text-sm rounded">{error}</div>
      )}

      <div className="space-y-lg">
        {/* LLM 配置卡片 */}
        <div className="border border-border rounded-lg p-lg bg-surface-elevated">
          <h2 className="text-base font-medium text-text mb-md">AI 模型配置</h2>

          {/* Provider */}
          <label className="block text-sm text-text-subtle mb-xs">LLM Provider</label>
          <select
            value={llmProvider}
            onChange={(e) => handleProviderChange(e.target.value)}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          >
            {Object.entries(PROVIDER_CONFIG).map(([key, val]) => (
              <option key={key} value={key}>
                {val.name}
              </option>
            ))}
          </select>

          {/* Model */}
          <label className="block text-sm text-text-subtle mb-xs">模型</label>
          <input
            value={llmModel}
            onChange={(e) => setLlmModel(e.target.value)}
            placeholder={PROVIDER_CONFIG[llmProvider]?.models[0] || "输入模型名"}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          />

          {/* API Key */}
          <label className="block text-sm text-text-subtle mb-xs">API Key</label>
          <input
            type="password"
            value={llmApiKey}
            onChange={(e) => setLlmApiKey(e.target.value)}
            placeholder={config?.has_llm_key ? "已保存，留空则不更新" : "sk-..."}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          />

          {/* Base URL */}
          <label className="block text-sm text-text-subtle mb-xs">Base URL（可选）</label>
          <input
            value={llmBaseUrl}
            onChange={(e) => setLlmBaseUrl(e.target.value)}
            placeholder={PROVIDER_CONFIG[llmProvider]?.baseUrl || "https://..."}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          />

          {/* 测试连接 */}
          <div className="flex items-center gap-sm mt-sm">
            <button
              onClick={handleTest}
              disabled={testing}
              className="px-md py-xs border border-border rounded text-sm text-text hover:bg-surface disabled:opacity-40"
            >
              {testing ? "测试中..." : "测试连接"}
            </button>
            {testResult && (
              <span className={`text-sm ${testResult.ok ? "text-green" : "text-red"}`}>
                {testResult.ok ? `✓ 连接成功 (${testResult.latency_ms}ms)` : `✗ ${testResult.error}`}
              </span>
            )}
          </div>
        </div>

        {/* Embedding 配置卡片 */}
        <div className="border border-border rounded-lg p-lg bg-surface-elevated">
          <h2 className="text-base font-medium text-text mb-md">Embedding 配置</h2>

          {/* Provider */}
          <label className="block text-sm text-text-subtle mb-xs">Embedding Provider</label>
          <select
            value={embeddingProvider}
            onChange={(e) => handleProviderChange(e.target.value, true)}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          >
            {Object.entries(PROVIDER_CONFIG).map(([key, val]) => (
              <option key={key} value={key}>
                {val.name}
              </option>
            ))}
          </select>

          {/* Model */}
          <label className="block text-sm text-text-subtle mb-xs">模型</label>
          <input
            value={embeddingModel}
            onChange={(e) => setEmbeddingModel(e.target.value)}
            placeholder={PROVIDER_CONFIG[embeddingProvider]?.embeddingModels[0] || "输入模型名"}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          />

          {/* API Key */}
          <label className="block text-sm text-text-subtle mb-xs">API Key（可选，留空则使用 LLM Key）</label>
          <input
            type="password"
            value={embeddingApiKey}
            onChange={(e) => setEmbeddingApiKey(e.target.value)}
            placeholder={config?.has_embedding_key ? "已保存，留空则不更新" : "sk-..."}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface mb-sm"
          />

          {/* Base URL */}
          <label className="block text-sm text-text-subtle mb-xs">Base URL（可选）</label>
          <input
            value={embeddingBaseUrl}
            onChange={(e) => setEmbeddingBaseUrl(e.target.value)}
            placeholder={PROVIDER_CONFIG[embeddingProvider]?.baseUrl || "https://..."}
            className="w-full px-sm py-xs border border-border rounded text-sm bg-surface"
          />
        </div>

        {/* 记忆管理卡片 */}
        <div className="border border-border rounded-lg p-lg bg-surface-elevated">
          <h2 className="text-base font-medium text-text mb-md">记忆管理</h2>

          <div className="flex items-center gap-sm mb-md">
            <span className="text-sm text-text-subtle">状态：</span>
            <span
              className={`text-sm ${memStats?.status === "ready" ? "text-green" : "text-text-subtle"}`}
            >
              {memStats?.status === "ready"
                ? "就绪"
                : memStats?.status === "no_api_key"
                  ? "未配置 Key"
                  : memStats?.status === "error"
                    ? "异常"
                    : "初始化中"}
            </span>
            <span className="text-sm text-text-subtle ml-sm">
              已记忆 <span className="text-text font-medium">{memStats?.count ?? "—"}</span> 条
            </span>
          </div>

          {!memConfirming ? (
            <button
              onClick={() => {
                setMemConfirming(true);
                setMemResetMsg("");
              }}
              disabled={!memStats || memStats.count === 0}
              className="px-md py-xs border border-border rounded text-sm text-text hover:bg-surface disabled:opacity-40"
            >
              清空记忆
            </button>
          ) : (
            <div className="flex items-center gap-sm">
              <span className="text-sm text-text-subtle">清空后 AI 将忘记所有记录，确认吗？</span>
              <button
                onClick={handleMemReset}
                disabled={memResetting}
                className="px-md py-xs bg-red/80 text-surface rounded text-sm disabled:opacity-40"
              >
                {memResetting ? "清空中..." : "确认清空"}
              </button>
              <button
                onClick={() => setMemConfirming(false)}
                className="px-md py-xs border border-border rounded text-sm text-text"
              >
                取消
              </button>
            </div>
          )}

          {memResetMsg && (
            <p
              className={`mt-xs text-sm ${memResetMsg.includes("失败") ? "text-red" : "text-green"}`}
            >
              {memResetMsg}
            </p>
          )}
        </div>

        {/* 保存按钮 */}
        <div className="flex items-center gap-sm">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-lg py-xs bg-ink text-surface rounded text-sm disabled:opacity-40"
          >
            {saving ? "保存中..." : saved ? "已保存" : "保存配置"}
          </button>
          {saved && <span className="text-sm text-green">✓ 配置已保存</span>}
        </div>
      </div>
    </div>
  );
}
