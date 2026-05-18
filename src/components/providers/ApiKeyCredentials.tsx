import { useState, useEffect } from 'react';
import { useProvidersStore, type ProviderSummary } from '../../store/providersStore';
import { testProvider } from '../../lib/api/providers';
import { updateConfig } from '../../lib/api/config';
import { KeyInput } from '../ui/KeyInput';
import { API_FORMAT_OPTIONS } from './ProvidersTab';

export function ApiKeyCredentials({ providerId, summary, isPresetSetup, presetInfo, onRefresh }: {
  providerId: string;
  summary: ProviderSummary;
  isPresetSetup?: boolean;
  presetInfo?: { label: string; value: string; url?: string; api?: string; local?: boolean };
  onRefresh: () => Promise<void>;
}) {
  const { showToast } = useProvidersStore();
  const [keyVal, setKeyVal] = useState('');
  const [keyEdited, setKeyEdited] = useState(false);
  const derivedBaseUrl = summary.base_url || presetInfo?.url || '';
  const [urlVal, setUrlVal] = useState(derivedBaseUrl);
  const [urlEdited, setUrlEdited] = useState(false);
  const api = summary.api || presetInfo?.api || '';

  useEffect(() => {
    if (!keyEdited) setKeyVal(summary.api_key || '');
  }, [summary.api_key, keyEdited]);

  useEffect(() => {
    if (!urlEdited) setUrlVal(derivedBaseUrl);
  }, [derivedBaseUrl, urlEdited]);

  const verifyAndSave = async () => {
    const shouldVerify = keyEdited || !!isPresetSetup;
    const payload: Record<string, any> = {};
    if (keyEdited) payload.api_key = keyVal.trim();
    if (urlEdited) payload.base_url = urlVal.trim();

    if (!payload.api_key && !payload.base_url && !isPresetSetup) return;

    try {
      if (shouldVerify) {
        const testData = await testProvider({ name: providerId, base_url: urlVal.trim() || derivedBaseUrl, api, api_key: keyVal.trim() });
        if (!testData.ok) {
          showToast('验证失败', 'error');
          return;
        }
      }

      if (isPresetSetup && (summary.models?.length ?? 0) === 0) {
        payload.seed_default_models = true;
      }

      await updateConfig({ providers: { [providerId]: payload } });
      showToast(shouldVerify ? '验证通过并已保存' : '已保存', 'success');
      if (isPresetSetup) useProvidersStore.setState({ selectedProviderId: providerId });
      setKeyEdited(false);
      if (urlEdited) setUrlEdited(false);
      await onRefresh();
    } catch {
      showToast('保存失败', 'error');
    }
  };

  const [connStatus, setConnStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');

  const verifyOnly = async () => {
    setConnStatus('testing');
    try {
      const testData = await testProvider({ name: providerId, base_url: urlVal.trim() || derivedBaseUrl, api, api_key: keyVal.trim() || undefined });
      setConnStatus(testData.ok ? 'ok' : 'fail');
      showToast(testData.ok ? '验证通过' : '验证失败', testData.ok ? 'success' : 'error');
    } catch {
      setConnStatus('fail');
      showToast('验证失败', 'error');
    }
  };

  const apiLabel = API_FORMAT_OPTIONS.find(o => o.value === api)?.label || api;

  return (
    <div className="flex flex-col gap-3 mb-2">
      {/* API Key */}
      <div className="flex items-center gap-4">
        <span className="w-[72px] text-sm text-text-subtle shrink-0 text-right">API Key</span>
        <div className="flex items-center gap-2">
          <KeyInput
            value={keyVal}
            onChange={(v) => { setKeyVal(v); setKeyEdited(true); setConnStatus('idle'); }}
            placeholder={isPresetSetup ? '首次设置请输入 Key' : ''}
          />
          <button
            className={`p-2 rounded-sm border border-border text-text-subtle hover:text-text transition-colors ${
              connStatus === 'ok' ? 'text-success border-success' : connStatus === 'fail' ? 'text-danger border-danger' : ''
            }`}
            title="验证并保存"
            onClick={() => {
              if (keyEdited && (keyVal.trim() || presetInfo?.local)) {
                verifyAndSave();
              } else {
                verifyOnly();
              }
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
            </svg>
          </button>
        </div>
      </div>

      {/* Base URL */}
      <div className="flex items-center gap-4">
        <span className="w-[72px] text-sm text-text-subtle shrink-0 text-right">Base URL</span>
        <span className="text-sm text-text">{urlVal}</span>
      </div>

      {/* API 类型 */}
      <div className="flex items-center gap-4">
        <span className="w-[72px] text-sm text-text-subtle shrink-0 text-right">API 类型</span>
        <span className="text-sm text-text">{apiLabel}</span>
      </div>
    </div>
  );
}
