import { useMemo } from 'react';
import { useProvidersStore } from '../../store/providersStore';

export function OtherModelsSection() {
  const { providersSummary, toolModel, heavyModel, setState } = useProvidersStore();

  // 聚合所有已配置供应商的模型
  const availableModels = useMemo(() => {
    const models: { id: string; provider: string; displayName: string }[] = [];
    Object.entries(providersSummary).forEach(([providerId, summary]) => {
      if (!summary.has_credentials) return;
      (summary.models || []).forEach((m: any) => {
        const modelId = typeof m === 'object' ? m.id : m;
        const displayName = typeof m === 'object' ? (m.displayName || m.name || modelId) : modelId;
        models.push({
          id: `${providerId}/${modelId}`,
          provider: providerId,
          displayName: `${displayName} (${providerId})`,
        });
      });
    });
    return models;
  }, [providersSummary]);

  const hasModels = availableModels.length > 0;

  const handleToolModelChange = (value: string) => {
    localStorage.setItem('lumen_tool_model', value);
    setState({ toolModel: value });
  };

  const handleHeavyModelChange = (value: string) => {
    localStorage.setItem('lumen_heavy_model', value);
    setState({ heavyModel: value });
  };

  return (
    <div className="space-y-md">
      <h3 className="text-sm font-medium text-ink">其他模型</h3>
      <div className="grid grid-cols-2 gap-md">
        {/* 小工具模型 */}
        <div className="p-lg rounded-sm border border-border bg-surface">
          <div className="text-sm font-medium text-ink mb-sm">小工具模型</div>
          <div className="flex items-center gap-sm">
            <select
              value={toolModel || ''}
              onChange={(e) => handleToolModelChange(e.target.value)}
              disabled={!hasModels}
              className="flex-1 px-sm py-2 border border-border rounded-sm text-sm bg-surface-elevated outline-none focus:border-ink transition-colors disabled:opacity-50"
            >
              <option value="">{hasModels ? '选择模型' : '请先配置供应商模型'}</option>
              {availableModels.map(m => (
                <option key={m.id} value={m.id}>{m.displayName}</option>
              ))}
            </select>
            <button
              className="p-2 rounded-sm border border-border text-text-subtle hover:text-text transition-colors shrink-0 disabled:opacity-40"
              title="验证"
              disabled={!hasModels}
              onClick={() => {
                if (toolModel) {
                  useProvidersStore.getState().showToast('已选择小工具模型', 'success');
                }
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
            </button>
          </div>
          <p className="mt-sm text-xs text-text-subtle leading-relaxed">
            生成标题、轻量分类等简单任务，推荐用便宜的小模型
          </p>
        </div>

        {/* 大工具模型 */}
        <div className="p-lg rounded-sm border border-border bg-surface">
          <div className="text-sm font-medium text-ink mb-sm">大工具模型</div>
          <div className="flex items-center gap-sm">
            <select
              value={heavyModel || ''}
              onChange={(e) => handleHeavyModelChange(e.target.value)}
              disabled={!hasModels}
              className="flex-1 px-sm py-2 border border-border rounded-sm text-sm bg-surface-elevated outline-none focus:border-ink transition-colors disabled:opacity-50"
            >
              <option value="">{hasModels ? '选择模型' : '请先配置供应商模型'}</option>
              {availableModels.map(m => (
                <option key={m.id} value={m.id}>{m.displayName}</option>
              ))}
            </select>
            <button
              className="p-2 rounded-sm border border-border text-text-subtle hover:text-text transition-colors shrink-0 disabled:opacity-40"
              title="验证"
              disabled={!hasModels}
              onClick={() => {
                if (heavyModel) {
                  useProvidersStore.getState().showToast('已选择大工具模型', 'success');
                }
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
            </button>
          </div>
          <p className="mt-sm text-xs text-text-subtle leading-relaxed">
            活动摘要、任务拆解等需要理解力的场景，用强大的大模型
          </p>
        </div>
      </div>
    </div>
  );
}
