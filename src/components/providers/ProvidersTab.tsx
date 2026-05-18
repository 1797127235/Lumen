import { useState, useCallback, useEffect, useMemo } from 'react';
import { useProvidersStore, type ProviderSummary } from '../../store/providersStore';
import { getProvidersSummary } from '../../lib/api/providers';
import { ProviderDetail } from './ProviderDetail';
import { AddCustomButton, AddProviderOverlay } from './ProviderList';
import { OtherModelsSection } from './OtherModelsSection';

export function ProvidersTab() {
  const { providersSummary, selectedProviderId } = useProvidersStore();
  const [addingProvider, setAddingProvider] = useState(false);

  const loadSummary = useCallback(async () => {
    try {
      const data = await getProvidersSummary();
      useProvidersStore.setState({ providersSummary: data.providers || {} });
    } catch { /* swallow */ }
  }, []);

  useEffect(() => { loadSummary(); }, [loadSummary]);

  const providerIds = Object.keys(providersSummary);
  const selected = selectedProviderId;

  const registeredSet = new Set(providerIds);
  const presetValues = new Set(PROVIDER_PRESETS.map(p => p.value));
  const customProviders = providerIds.filter(id => !presetValues.has(id) && providersSummary[id].can_delete);
  const presetProviders = providerIds.filter(id => presetValues.has(id));
  const unregisteredPresets = PROVIDER_PRESETS.filter(p => !registeredSet.has(p.value));

  const configuredPresets = useMemo(() =>
    presetProviders.filter(id => providersSummary[id]?.has_credentials),
  [presetProviders, providersSummary]);

  const unconfiguredPresets = useMemo(() =>
    presetProviders.filter(id => !providersSummary[id]?.has_credentials),
  [presetProviders, providersSummary]);

  const selectProvider = (id: string) => {
    useProvidersStore.setState({ selectedProviderId: id });
  };

  const renderItem = (id: string, p: ProviderSummary, isRegistered: boolean) => {
    const preset = PROVIDER_PRESETS.find(pr => pr.value === id);
    const modelCount = (p.models || []).length;
    const isSelected = selected === id;
    return (
      <button
        key={id}
        className={`group relative flex items-center gap-2.5 px-2 py-[9px] cursor-pointer text-sm text-left transition-all duration-150 w-full rounded-r-lg ${
          isSelected
            ? 'bg-surface-elevated text-accent'
            : isRegistered
              ? 'text-text hover:bg-surface-elevated/60'
              : 'text-text-muted hover:text-text hover:bg-surface-elevated/40'
        }`}
        onClick={() => selectProvider(id)}
      >
        {isSelected && (
          <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[2.5px] h-5 rounded-full bg-accent" />
        )}
        <span className={`w-2 h-2 rounded-full shrink-0 ${p.has_credentials ? 'bg-success' : 'bg-border'}`} />
        <span className="flex-1 truncate font-medium">{preset?.label || p.display_name || id}</span>
        <span className="text-xs text-text-subtle tabular-nums shrink-0 ml-1">{modelCount}</span>
      </button>
    );
  };

  const renderUnregisteredItem = (preset: typeof PROVIDER_PRESETS[0]) => {
    const summary: ProviderSummary = {
      type: 'api-key',
      auth_type: 'api-key',
      display_name: preset.label,
      base_url: preset.url || '',
      api: preset.api || '',
      api_key: '',
      models: [],
      custom_models: [],
      has_credentials: false,
      supports_oauth: false,
      can_delete: false,
    };
    return renderItem(preset.value, summary, false);
  };

  return (
    <div className="space-y-6">
      {/* 供应商卡片 */}
      <div className="rounded-sm border border-border bg-surface overflow-hidden">
        <div className="flex h-[460px]">
          {/* 左栏 - 分组列表 */}
          <div className="w-[240px] border-r border-border overflow-y-auto flex-shrink-0 px-2 py-2">
            {/* API 分组标题 */}
            <div className="text-[10px] text-text-subtle px-2 py-2 uppercase tracking-[0.12em] font-semibold">
              API
            </div>

            {/* 所有预设供应商 */}
            {configuredPresets.map(id => renderItem(id, providersSummary[id], true))}
            {unconfiguredPresets.map(id => renderItem(id, providersSummary[id], true))}
            {unregisteredPresets.map(preset => renderUnregisteredItem(preset))}

            {/* 自定义分组 */}
            {customProviders.length > 0 && (
              <>
                <div className="text-[10px] text-text-subtle px-2 py-2 uppercase tracking-[0.12em] font-semibold mt-1">
                  自定义
                </div>
                {customProviders.map(id => renderItem(id, providersSummary[id], true))}
              </>
            )}

            <AddCustomButton onClick={() => setAddingProvider(true)} />
          </div>

          {/* 右栏 - 详情 */}
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {selected ? (() => {
              const existing = providersSummary[selected];
              const preset = PROVIDER_PRESETS.find(p => p.value === selected);
              const summary: ProviderSummary = existing || {
                type: 'api-key',
                auth_type: 'api-key',
                display_name: preset?.label || selected,
                base_url: preset?.url || '',
                api: preset?.api || '',
                api_key: '',
                models: [],
                custom_models: [],
                has_credentials: false,
                supports_oauth: false,
                can_delete: false,
              };
              return (
                <ProviderDetail
                  key={selected}
                  providerId={selected}
                  summary={summary}
                  isPresetSetup={!existing && !!preset}
                  presetInfo={preset}
                  onRefresh={loadSummary}
                />
              );
            })() : (
              <div className="h-full flex items-center justify-center">
                <div className="text-sm text-text-subtle text-center">
                  选择一个供应商查看详情
                </div>
              </div>
            )}
          </div>
        </div>

        {/* 添加自定义供应商 overlay */}
        {addingProvider && (
          <AddProviderOverlay
            onDone={() => { setAddingProvider(false); loadSummary(); }}
            onCancel={() => setAddingProvider(false)}
          />
        )}
      </div>

      {/* 其他模型区域 */}
      <OtherModelsSection />
    </div>
  );
}

export const PROVIDER_PRESETS = [
  { value: 'anthropic', label: 'Anthropic', url: 'https://api.anthropic.com', api: 'anthropic-messages' },
  { value: 'openai', label: 'OpenAI', url: 'https://api.openai.com/v1', api: 'openai-completions' },
  { value: 'gemini', label: 'Google Gemini', url: 'https://generativelanguage.googleapis.com/v1beta', api: 'google-generative-ai' },
  { value: 'xai', label: 'xAI (Grok)', url: 'https://api.x.ai/v1', api: 'openai-completions' },
  { value: 'mistral', label: 'Mistral AI', url: 'https://api.mistral.ai/v1', api: 'openai-completions' },
  { value: 'groq', label: 'Groq', url: 'https://api.groq.com/openai/v1', api: 'openai-completions' },
  { value: 'perplexity', label: 'Perplexity', url: 'https://api.perplexity.ai', api: 'openai-completions' },
  { value: 'fireworks', label: 'Fireworks AI', url: 'https://api.fireworks.ai/inference/v1', api: 'openai-completions' },
  { value: 'together', label: 'Together AI', url: 'https://api.together.xyz/v1', api: 'openai-completions' },
  { value: 'openrouter', label: 'OpenRouter', url: 'https://openrouter.ai/api/v1', api: 'openai-completions' },
  { value: 'dashscope', label: '阿里云百炼', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', api: 'openai-completions' },
  { value: 'deepseek', label: 'DeepSeek', url: 'https://api.deepseek.com', api: 'openai-completions' },
  { value: 'moonshot', label: 'Moonshot (Kimi)', url: 'https://api.moonshot.cn/v1', api: 'openai-completions' },
  { value: 'zhipu', label: '智谱 AI', url: 'https://open.bigmodel.cn/api/paas/v4', api: 'openai-completions' },
  { value: 'volcengine', label: '火山引擎', url: 'https://ark.cn-beijing.volces.com/api/v3', api: 'openai-completions' },
  { value: 'siliconflow', label: 'SiliconFlow', url: 'https://api.siliconflow.cn/v1', api: 'openai-completions' },
  { value: 'baichuan', label: '百川智能', url: 'https://api.baichuan-ai.com/v1', api: 'openai-completions' },
  { value: 'hunyuan', label: '腾讯混元', url: 'https://api.hunyuan.cloud.tencent.com/v1', api: 'openai-completions' },
  { value: 'minimax', label: 'MiniMax', url: 'https://api.minimaxi.com/anthropic', api: 'anthropic-messages' },
  { value: 'baidu-cloud', label: '百度智能云', url: 'https://qianfan.baidubce.com/v2', api: 'openai-completions' },
  { value: 'stepfun', label: '阶跃星辰', url: 'https://api.stepfun.com/v1', api: 'openai-completions' },
  { value: 'modelscope', label: '魔搭', url: 'https://api-inference.modelscope.cn/v1', api: 'openai-completions' },
  { value: 'infini', label: '无问芯穹', url: 'https://cloud.infini-ai.com/maas/v1', api: 'openai-completions' },
  { value: 'mimo', label: 'Xiaomi (MiMo)', url: 'https://api.xiaomimimo.com/v1', api: 'openai-completions' },
  { value: 'ollama', label: 'Ollama（本地）', url: 'http://localhost:11434/v1', api: 'openai-completions', local: true },
  { value: 'custom', label: '自定义', url: '', api: 'openai-completions' },
];

export const API_FORMAT_OPTIONS = [
  { value: 'openai-completions', label: 'OpenAI Compatible' },
  { value: 'google-generative-ai', label: 'Google Gemini' },
  { value: 'anthropic-messages', label: 'Anthropic Messages' },
  { value: 'openai-responses', label: 'OpenAI Responses' },
];

export const CONTEXT_PRESETS = [
  { label: '64K', value: 65536 },
  { label: '128K', value: 131072 },
  { label: '200K', value: 200000 },
  { label: '256K', value: 262144 },
  { label: '1M', value: 1048576 },
];

export const OUTPUT_PRESETS = [
  { label: '8K', value: 8192 },
  { label: '16K', value: 16384 },
  { label: '32K', value: 32768 },
  { label: '64K', value: 65536 },
];

export function formatContext(n: number): string {
  if (!n) return '';
  if (n >= 1000000) {
    const m = n / 1000000;
    return (Number.isInteger(m) ? m : +m.toFixed(1)) + 'M';
  }
  const k = n / 1024;
  if (Number.isInteger(k)) return k + 'K';
  return Math.round(n / 1000) + 'K';
}

export function lookupModelMeta(modelId: string, provider?: string): any {
  const { providersSummary } = useProvidersStore.getState();
  if (!providersSummary) return null;
  const summaries = provider ? [providersSummary[provider]] : Object.values(providersSummary);
  for (const summary of summaries) {
    if (!summary) continue;
    const found = (summary.models || []).find(
      (m: any) => typeof m === 'object' && m?.id === modelId,
    );
    if (found) return found as Record<string, any>;
  }
  return null;
}
