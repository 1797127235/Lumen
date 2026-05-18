import { useState } from 'react';
import { useProvidersStore } from '../../store/providersStore';
import { updateModelMeta } from '../../lib/api/providers';
import { ComboInput, Toggle } from '../ui';
import { CONTEXT_PRESETS, OUTPUT_PRESETS } from './ProvidersTab';

export function ModelEditPanel({ modelId, providerId, onClose, onRefresh }: {
  modelId: string;
  providerId: string;
  onClose: () => void;
  onRefresh?: () => Promise<void>;
}) {
  const { showToast } = useProvidersStore();
  const [displayName, setDisplayName] = useState('');
  const [ctxVal, setCtxVal] = useState('');
  const [outVal, setOutVal] = useState('');
  const [image, setImage] = useState(false);
  const [video, setVideo] = useState(false);
  const [reasoning, setReasoning] = useState(false);

  const save = async () => {
    const entry: Record<string, any> = {};
    const name = displayName.trim();
    const ctx = ctxVal.trim();
    const maxOut = outVal.trim();
    if (name) entry.name = name;
    if (ctx) entry.context = parseInt(ctx);
    if (maxOut) entry.maxOutput = parseInt(maxOut);
    entry.image = image;
    entry.video = video;
    entry.reasoning = reasoning;

    try {
      await updateModelMeta(providerId, modelId, entry);
      showToast('已保存', 'success');
      await onRefresh?.();
      onClose();
    } catch {
      showToast('保存失败', 'error');
    }
  };

  return (
    <>
      <div className="fixed inset-0 z-30 bg-bg/30" onClick={onClose} />
      <div className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-40 w-[360px] rounded-xl border border-border bg-surface shadow-xl p-4 space-y-3">
        <div>
          <label className="block text-xs text-text-subtle mb-1">ID</label>
          <span className="text-sm text-text font-mono">{modelId}</span>
        </div>
        <div>
          <label className="block text-xs text-text-subtle mb-1">显示名称</label>
          <input
            className="w-full px-sm py-2 border border-border rounded-lg text-sm bg-surface-elevated outline-none focus:border-ink"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={modelId}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-text-subtle mb-1">上下文长度</label>
            <ComboInput presets={CONTEXT_PRESETS} value={ctxVal} onChange={setCtxVal} placeholder="131072" />
          </div>
          <div>
            <label className="block text-xs text-text-subtle mb-1">最大输出</label>
            <ComboInput presets={OUTPUT_PRESETS} value={outVal} onChange={setOutVal} placeholder="16384" />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-text-subtle mb-1">图像</label>
            <Toggle on={image} onChange={setImage} />
          </div>
          <div>
            <label className="block text-xs text-text-subtle mb-1">视频</label>
            <Toggle on={video} onChange={setVideo} />
          </div>
          <div>
            <label className="block text-xs text-text-subtle mb-1">推理</label>
            <Toggle on={reasoning} onChange={setReasoning} />
          </div>
        </div>
        <div className="flex gap-2 justify-end pt-2">
          <button className="px-3 py-1.5 text-xs border border-border rounded-lg" onClick={onClose}>取消</button>
          <button className="px-3 py-1.5 text-xs bg-ink text-bg rounded-lg" onClick={save}>保存</button>
        </div>
      </div>
    </>
  );
}
