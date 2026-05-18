import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useProvidersStore, type ProviderSummary } from '../../store/providersStore';
import { fetchModels, getDiscoveredModels } from '../../lib/api/providers';
import { updateConfig } from '../../lib/api/config';
import { formatContext, lookupModelMeta } from './ProvidersTab';
import { ModelEditPanel } from './ModelEditPanel';

interface DiscoveredModel {
  id: string;
  name?: string;
  context?: number | null;
  maxOutput?: number | null;
}

type CapabilityKind = 'image' | 'video' | 'reasoning';

function CapabilityIcon({ kind }: { kind: CapabilityKind }) {
  return (
    <span className="text-text-subtle" title={kind}>
      {kind === 'image' ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <path d="M21 15l-5-5L5 21" />
        </svg>
      ) : kind === 'video' ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="5" width="13" height="14" rx="2" />
          <path d="m16 9 5-3v12l-5-3" />
        </svg>
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 18h6" />
          <path d="M10 22h4" />
          <path d="M12 2a7 7 0 0 0-4 12.74V16a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-1.26A7 7 0 0 0 12 2Z" />
        </svg>
      )}
    </span>
  );
}

export function ProviderModelList({ providerId, summary, onRefresh }: {
  providerId: string;
  summary: ProviderSummary;
  onRefresh: () => Promise<void>;
}) {
  const { showToast } = useProvidersStore();
  const [search, setSearch] = useState('');
  const [customInput, setCustomInput] = useState('');
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModel[]>([]);

  const loadDiscoveredModels = async () => {
    try {
      const data = await getDiscoveredModels(providerId);
      setDiscoveredModels(data.models || []);
    } catch {
      // cache miss is fine
    }
  };

  useEffect(() => { loadDiscoveredModels(); }, [providerId]);

  const rawModels = summary.models || [];
  const modelId = (m: any): string => typeof m === 'object' ? m.id : m;
  const currentModelIds = rawModels.map(modelId);
  const discoveredIds = discoveredModels.map(m => m.id);
  const allModels = [...new Set([...currentModelIds, ...discoveredIds, ...(summary.custom_models || [])])];
  const query = search.toLowerCase();
  const filtered = query ? allModels.filter(m => m.toLowerCase().includes(query)) : allModels;

  const addModelToProvider = async (mid: string) => {
    if (currentModelIds.includes(mid)) return;
    try {
      await updateConfig({ providers: { [providerId]: { models: [...rawModels, mid] } } });
      await onRefresh();
    } catch {
      showToast('添加失败', 'error');
    }
  };

  const removeModelFromProvider = async (mid: string) => {
    try {
      const next = rawModels.filter((m: any) => (typeof m === 'object' ? m.id : m) !== mid);
      await updateConfig({ providers: { [providerId]: { models: next } } });
      await onRefresh();
    } catch {
      showToast('移除失败', 'error');
    }
  };

  const setAsCurrentModel = async (mid: string) => {
    try {
      await updateConfig({ llm_provider: providerId, llm_model: mid });
      showToast(`已设为当前模型: ${mid}`, 'success');
    } catch {
      showToast('设置失败', 'error');
    }
  };

  const addCustomModel = async () => {
    const id = customInput.trim();
    if (!id) return;
    try {
      await updateConfig({ providers: { [providerId]: { models: [...rawModels, id] } } });
      setCustomInput('');
      await onRefresh();
    } catch {
      showToast('添加失败', 'error');
    }
  };

  const [fetchHint, setFetchHint] = useState<{ msg: string; ok: boolean } | null>(null);
  const fetchHintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showFetchHint = (msg: string, ok: boolean) => {
    if (fetchHintTimer.current) clearTimeout(fetchHintTimer.current);
    setFetchHint({ msg, ok });
    fetchHintTimer.current = setTimeout(() => setFetchHint(null), 2500);
  };

  const doFetchModels = async () => {
    try {
      const data = await fetchModels({ name: providerId, base_url: summary.base_url, api: summary.api });
      if (data.error) { showFetchHint('获取模型列表失败', false); return; }
      const models = (data.models || []) as DiscoveredModel[];
      if (models.length === 0) { showFetchHint('未获取到模型', false); return; }
      setDiscoveredModels(models);
      showFetchHint(`获取到 ${models.length} 个模型`, true);
    } catch {
      showFetchHint('获取模型列表失败', false);
    }
  };

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [dropPos, setDropPos] = useState<{ top: number; left: number; width: number } | null>(null);

  const openDropdown = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setDropPos({ top: rect.bottom + 4, left: rect.left, width: rect.width + 80 });
    }
    setDropdownOpen(true);
  };

  const closeDropdown = useCallback(() => setDropdownOpen(false), []);

  useEffect(() => {
    if (!dropdownOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (target instanceof Element && target.closest('[data-provider-model-dropdown="true"]')) return;
      closeDropdown();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [dropdownOpen, closeDropdown]);

  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div>
      {currentModelIds.length > 0 && (
        <div className="mb-3">
          <div className="flex items-center gap-2 text-sm font-medium text-ink mb-2">
            已添加模型
            <span className="text-xs text-text-subtle">{currentModelIds.length}</span>
          </div>
          <div className="space-y-1">
            {currentModelIds.map(mid => {
              const meta = lookupModelMeta(mid, providerId) || {};
              const displayName = meta.displayName || meta.name || mid;
              const showModelId = displayName !== mid;
              return (
                <div key={mid} className="flex items-center gap-2 px-2 py-1.5 rounded-sm border border-border-soft bg-surface/50">
                  <span className="text-sm text-text truncate flex-1" title={String(displayName)}>{displayName}</span>
                  {showModelId && <span className="text-xs text-text-subtle truncate max-w-[120px]" title={mid}>{mid}</span>}
                  {meta.image === true && <CapabilityIcon kind="image" />}
                  {meta.video === true && <CapabilityIcon kind="video" />}
                  {meta.reasoning === true && <CapabilityIcon kind="reasoning" />}
                  {meta.context && <span className="text-xs text-text-subtle">{formatContext(meta.context)}</span>}
                  <div className="flex items-center gap-1">
                    <button
                      className="p-1 text-xs text-text-subtle hover:text-accent"
                      title="设为当前模型"
                      onClick={() => setAsCurrentModel(mid)}
                    >
                      使用
                    </button>
                    <button
                      className="p-1 text-text-subtle hover:text-text"
                      title="编辑"
                      onClick={() => setEditing(mid)}
                    >
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                      </svg>
                    </button>
                    <button
                      className="p-1 text-text-subtle hover:text-danger"
                      onClick={() => removeModelFromProvider(mid)}
                      title="移除"
                    >
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          {editing && (
            <ModelEditPanel modelId={editing} providerId={providerId} onClose={() => setEditing(null)} onRefresh={onRefresh} />
          )}
        </div>
      )}

      <div className="flex items-center gap-2 mb-2">
        <button
          ref={triggerRef}
          className="px-3 py-1.5 text-sm border border-border rounded-sm text-text hover:bg-surface-elevated transition-colors"
          onClick={() => dropdownOpen ? closeDropdown() : openDropdown()}
        >
          <span>添加模型</span>
          <span className="ml-1">▾</span>
        </button>
        <button
          className="flex items-center gap-1 px-3 py-1.5 text-sm border border-border rounded-sm text-text hover:bg-surface-elevated transition-colors"
          title="从远程获取模型列表"
          onClick={doFetchModels}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
          获取模型列表
        </button>
      </div>

      {fetchHint && (
        <div className={`mb-2 text-xs ${fetchHint.ok ? 'text-success' : 'text-danger'}`}>{fetchHint.msg}</div>
      )}

      {dropdownOpen && dropPos && createPortal(
        <div
          className="rounded-sm border border-border bg-surface shadow-xl overflow-hidden"
          style={{ position: 'fixed', top: dropPos.top, left: dropPos.left, width: dropPos.width, zIndex: 9999 }}
          data-provider-model-dropdown="true"
        >
          <input
            className="w-full px-3 py-2 text-sm border-b border-border bg-surface-elevated outline-none"
            type="text"
            placeholder="搜索模型"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <div className="max-h-48 overflow-y-auto">
            {filtered.map(mid => {
              const isAdded = currentModelIds.includes(mid);
              const meta = lookupModelMeta(mid, providerId) || {};
              const discovered = discoveredModels.find(d => d.id === mid);
              const ctx = meta.context || discovered?.context;
              return (
                <button
                  key={mid}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-surface-elevated transition-colors ${isAdded ? 'opacity-50' : ''}`}
                  onClick={() => { if (!isAdded) { addModelToProvider(mid); } }}
                >
                  <span className="flex-1 truncate">{mid}</span>
                  {isAdded && <span className="text-success text-xs">{'\u2713'}</span>}
                  {ctx && <span className="text-xs text-text-subtle">{formatContext(ctx)}</span>}
                </button>
              );
            })}
            {filtered.length === 0 && (
              <div className="px-3 py-2 text-sm text-text-subtle">无结果</div>
            )}
          </div>
          <div className="flex items-center gap-1 p-2 border-t border-border">
            <input
              className="flex-1 px-2 py-1.5 text-sm border border-border rounded bg-surface-elevated outline-none focus:border-ink"
              type="text"
              placeholder="输入模型 ID"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { addCustomModel(); } }}
            />
            <button className="px-2 py-1.5 text-sm border border-border rounded hover:bg-surface-elevated" onClick={addCustomModel}>{'\u21B5'}</button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
