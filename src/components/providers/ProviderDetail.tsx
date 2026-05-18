import { useState } from 'react';
import { useProvidersStore, type ProviderSummary } from '../../store/providersStore';
import { updateConfig } from '../../lib/api/config';
import { ApiKeyCredentials } from './ApiKeyCredentials';
import { ProviderModelList } from './ProviderModelList';

export function ProviderDetail({ providerId, summary, isPresetSetup, presetInfo, onRefresh }: {
  providerId: string;
  summary: ProviderSummary;
  isPresetSetup?: boolean;
  presetInfo?: { label: string; value: string; url?: string; api?: string; local?: boolean };
  onRefresh: () => Promise<void>;
}) {
  return (
    <div className="relative">
      {/* 供应商名称大标题 */}
      <h2 className="text-lg font-semibold text-text mb-6">{summary.display_name || providerId}</h2>

      {/* 状态提示 */}
      {summary.config_status === 'invalid' && (
        <div className="mb-4 px-4 py-2.5 rounded-sm bg-danger/10 text-danger text-sm">
          配置无效
        </div>
      )}
      {summary.config_status === 'needs_setup' && summary.can_delete && !summary.config_error && (
        <div className="mb-4 px-4 py-2.5 rounded-sm bg-warning/10 text-warning text-sm">
          配置不完整
        </div>
      )}

      {/* 凭证表单 */}
      <ApiKeyCredentials
        providerId={providerId}
        summary={summary}
        isPresetSetup={isPresetSetup}
        presetInfo={presetInfo}
        onRefresh={onRefresh}
      />

      {/* 分割线 */}
      <div className="border-t border-border my-5" />

      {/* 模型列表 */}
      <ProviderModelList providerId={providerId} summary={summary} onRefresh={onRefresh} />

      {/* 删除按钮 */}
      {summary.can_delete && !isPresetSetup && (
        <div className="mt-6 pt-4 border-t border-border">
          <ProviderDeleteButton providerId={providerId} onRefresh={onRefresh} />
        </div>
      )}
    </div>
  );
}

function ProviderDeleteButton({ providerId, onRefresh }: { providerId: string; onRefresh: () => Promise<void> }) {
  const { showToast } = useProvidersStore();
  const [confirming, setConfirming] = useState(false);

  const handleDelete = async () => {
    try {
      await updateConfig({ providers: { [providerId]: null } });
      showToast('已删除', 'success');
      useProvidersStore.setState({ selectedProviderId: null });
      setConfirming(false);
      await onRefresh();
    } catch {
      showToast('删除失败', 'error');
    }
  };

  return (
    <>
      <button
        className="text-xs text-text-subtle hover:text-danger transition-colors"
        onClick={() => setConfirming(true)}
      >
        删除此供应商
      </button>
      {confirming && (
        <>
          <div className="fixed inset-0 z-30 bg-bg/30" onClick={() => setConfirming(false)} />
          <div className="absolute z-40 left-0 mt-2 p-4 rounded-sm border border-border bg-surface shadow-xl w-64">
            <p className="text-sm text-text mb-3">确定删除 "{providerId}" 吗？</p>
            <div className="flex gap-2 justify-end">
              <button className="px-3 py-1.5 text-xs border border-border rounded-sm" onClick={() => setConfirming(false)}>取消</button>
              <button className="px-3 py-1.5 text-xs bg-danger text-bg rounded-sm" onClick={handleDelete}>删除</button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
