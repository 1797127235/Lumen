import { useState } from 'react';
import { useProvidersStore } from '../../store/providersStore';
import { updateConfig } from '../../lib/api/config';
import { KeyInput } from '../ui/KeyInput';
import { API_FORMAT_OPTIONS } from './ProvidersTab';

export function AddCustomButton({ onClick }: { onClick: () => void }) {
  return (
    <div className="px-3 py-2">
      <button
        className="w-full text-left text-sm text-text-subtle hover:text-text transition-colors"
        onClick={onClick}
      >
        + 添加自定义供应商
      </button>
    </div>
  );
}

export function AddProviderOverlay({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  return (
    <div className="absolute inset-0 z-20 bg-surface/95 flex flex-col">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <button className="flex items-center gap-1 text-sm text-text-subtle hover:text-text" onClick={onCancel}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          <span>取消</span>
        </button>
        <div className="text-sm font-medium text-ink">添加自定义供应商</div>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        <AddProviderForm onDone={onDone} />
      </div>
    </div>
  );
}

function AddProviderForm({ onDone }: { onDone: () => void }) {
  const { showToast } = useProvidersStore();
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [api, setApi] = useState('openai-completions');

  const submit = async () => {
    const n = name.trim().toLowerCase();
    const u = url.trim();
    if (!n) { showToast('名称必填', 'error'); return; }
    if (!u) { showToast('Base URL 必填', 'error'); return; }
    try {
      await updateConfig({
        providers: { [n]: { base_url: u, api_key: apiKey.trim(), api, models: [] as string[] } },
      });
      showToast('已添加', 'success');
      useProvidersStore.setState({ selectedProviderId: n });
      onDone();
    } catch {
      showToast('添加失败', 'error');
    }
  };

  return (
    <div className="space-y-4 max-w-md">
      <div>
        <label className="block text-xs text-text-subtle mb-1">名称</label>
        <input
          className="w-full px-sm py-2 border border-border rounded-sm text-sm bg-surface-elevated outline-none focus:border-ink"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-provider"
        />
      </div>
      <div>
        <label className="block text-xs text-text-subtle mb-1">Base URL</label>
        <input
          className="w-full px-sm py-2 border border-border rounded-sm text-sm bg-surface-elevated outline-none focus:border-ink"
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://api.example.com/v1"
        />
      </div>
      <div>
        <label className="block text-xs text-text-subtle mb-1">API Key</label>
        <KeyInput value={apiKey} onChange={setApiKey} placeholder="sk-..." />
      </div>
      <div>
        <label className="block text-xs text-text-subtle mb-1">API 格式</label>
        <select
          className="w-full px-sm py-2 border border-border rounded-sm text-sm bg-surface-elevated outline-none focus:border-ink"
          value={api}
          onChange={(e) => setApi(e.target.value)}
        >
          {API_FORMAT_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
      <div className="flex gap-2">
        <button
          className="px-4 py-2 rounded-sm text-sm text-bg bg-ink hover:bg-ink-deep transition-colors"
          onClick={submit}
        >
          添加
        </button>
      </div>
    </div>
  );
}
