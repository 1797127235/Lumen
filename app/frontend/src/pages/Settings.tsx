import { useEffect, useRef, useState } from "react";
import { getConfig, updateConfig } from "../lib/api";

export default function Settings() {
  const [apiKey, setApiKey] = useState("");
  const [hasKey, setHasKey] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const savedTimer = useRef<number | null>(null);

  useEffect(() => {
    getConfig()
      .then((cfg) => {
        setHasKey(cfg.has_api_key);
      })
      .catch(() => setError("配置加载失败，请刷新重试"))
      .finally(() => setLoading(false));

    return () => {
      if (savedTimer.current) {
        window.clearTimeout(savedTimer.current);
      }
    };
  }, []);

  const handleSave = async () => {
    const key = apiKey.trim();
    if (!key || saving) return;
    setSaving(true);
    setError("");
    try {
      await updateConfig({ dashscope_api_key: key });
      setHasKey(true);
      setSaved(true);
      setApiKey("");
      savedTimer.current = window.setTimeout(() => setSaved(false), 2000);
    } catch {
      setError("保存失败，请检查网络或稍后重试");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;

  return (
    <div className="max-w-lg mx-auto px-md py-xl">
      <h1 className="text-xl font-han text-ink mb-md">设置</h1>

      {error && (
        <div className="mb-md px-sm py-xs bg-red/10 text-red text-sm rounded">
          {error}
        </div>
      )}

      <div className="space-y-lg">
        <div>
          <label className="block text-sm text-text-subtle mb-xs">DashScope API Key</label>
          {hasKey ? (
            <div className="flex items-center gap-sm text-sm text-green">
              <span className="w-2 h-2 rounded-full bg-green" />
              已配置
            </div>
          ) : (
            <p className="text-sm text-text-muted mb-sm">
              CareerOS 使用阿里云 DashScope 提供 AI 能力。
              <a
                href="https://dashscope.aliyun.com/"
                target="_blank"
                rel="noreferrer"
                className="text-blue ml-xs"
              >
                免费获取 API Key →
              </a>
            </p>
          )}
          <div className="flex gap-sm mt-sm">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={hasKey ? "输入新 Key 以更换" : "sk-..."}
              className="flex-1 px-sm py-xs border border-border rounded text-sm bg-surface"
            />
            <button
              onClick={handleSave}
              disabled={!apiKey.trim() || saving}
              className="px-md py-xs bg-ink text-surface rounded text-sm disabled:opacity-40"
            >
              {saving ? "保存中..." : saved ? "已保存" : "保存"}
            </button>
          </div>
        </div>

        <div>
          <label className="block text-sm text-text-subtle mb-xs">数据存储</label>
          <p className="text-sm text-text-muted font-mono">
            ~/.careeros/
            <br />
            ├── career_os.db
            <br />
            ├── chroma_db/
            <br />
            └── config.json
          </p>
          <p className="text-xs text-text-muted mt-xs">
            所有对话、画像、岗位数据均存储在此目录。备份或迁移只需复制整个 ~/.careeros 文件夹。
          </p>
        </div>
      </div>
    </div>
  );
}
