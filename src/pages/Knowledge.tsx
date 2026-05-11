import { useEffect, useRef, useState } from "react";
import {
  deleteKnowledgeFile,
  getKnowledgeFiles,
  getKnowledgeFileStatus,
  uploadKnowledgeFile,
  type KnowledgeFile,
} from "../lib/api";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusBadge(status: string) {
  const map: Record<string, { label: string; cls: string }> = {
    pending: { label: "等待中", cls: "bg-amber-500/10 text-amber-600" },
    processing: { label: "处理中", cls: "bg-sky-500/10 text-sky-600" },
    ready: { label: "就绪", cls: "bg-green-500/10 text-green-600" },
    failed: { label: "失败", cls: "bg-red-500/10 text-red-600" },
  };
  const s = map[status] || { label: status, cls: "bg-border text-text-subtle" };
  return (
    <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.label}
    </span>
  );
}

export default function Knowledge() {
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const pollTimers = useRef<Record<string, number>>({});
  const filesRef = useRef<KnowledgeFile[]>([]);
  filesRef.current = files;

  async function loadFiles() {
    setError("");
    try {
      const res = await getKnowledgeFiles();
      setFiles(res.files);
    } catch {
      setError("读取文件列表失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadFiles();
  }, []);

  // 轮询 processing 状态的文件
  useEffect(() => {
    const processingIds = new Set(
      files.filter((f) => f.status === "processing").map((f) => f.id),
    );

    // 启动新轮询
    processingIds.forEach((id) => {
      if (pollTimers.current[id]) return;
      pollTimers.current[id] = window.setInterval(async () => {
        try {
          // 通过 ref 获取最新文件状态，避免闭包问题
          const currentFile = filesRef.current.find((x) => x.id === id);
          if (!currentFile || currentFile.status !== "processing") {
            const timer = pollTimers.current[id];
            if (timer) {
              window.clearInterval(timer);
              delete pollTimers.current[id];
            }
            return;
          }

          const status = await getKnowledgeFileStatus(id);
          setFiles((prev) =>
            prev.map((item) =>
              item.id === id
                ? {
                    ...item,
                    status: status.status as KnowledgeFile["status"],
                    chunk_count: status.chunk_count,
                    preview: status.preview,
                    error_message: status.error_message,
                  }
                : item,
            ),
          );
          if (status.status === "ready" || status.status === "failed") {
            const timer = pollTimers.current[id];
            if (timer) {
              window.clearInterval(timer);
              delete pollTimers.current[id];
            }
          }
        } catch {
          // ignore poll errors
        }
      }, 2000);
    });

    // 清理非 processing 的轮询
    Object.entries(pollTimers.current).forEach(([id, timer]) => {
      if (!processingIds.has(id)) {
        window.clearInterval(timer);
        delete pollTimers.current[id];
      }
    });

    return () => {
      Object.values(pollTimers.current).forEach((t) => window.clearInterval(t));
      pollTimers.current = {};
    };
  }, [files]);

  async function handleUpload(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError("");
    try {
      const uploaded = await Promise.all(
        Array.from(fileList).map((file) => uploadKnowledgeFile(file)),
      );
      const newFiles: KnowledgeFile[] = uploaded.map((u) => ({
        id: u.id,
        filename: u.filename,
        file_type: u.filename.split(".").pop() || "",
        size_bytes: 0,
        status: "pending",
        chunk_count: 0,
        preview: null,
        error_message: null,
        created_at: new Date().toISOString(),
      }));
      setFiles((prev) => [...newFiles, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: string) {
    setDeleting(id);
    setError("");
    try {
      await deleteKnowledgeFile(id);
      setFiles((prev) => prev.filter((f) => f.id !== id));
      setConfirmDelete(null);
    } catch {
      setError("删除失败，请稍后重试");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="mx-auto max-w-[720px] px-md py-xl min-h-full">
      <div className="mb-lg flex items-center justify-between gap-md">
        <h1 className="text-xl font-han text-ink">文档管理</h1>
        <button
          onClick={() => {
            setLoading(true);
            void loadFiles();
          }}
          className="rounded border border-border px-sm py-1 text-sm text-text hover:bg-surface"
        >
          刷新
        </button>
      </div>

      {/* 上传区域 */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void handleUpload(e.dataTransfer.files);
        }}
        className={`mb-lg rounded-xl border-2 border-dashed px-md py-lg text-center transition-colors ${
          dragOver
            ? "border-ink/40 bg-surface-elevated"
            : "border-border-soft bg-surface/50"
        }`}
      >
        <input
          type="file"
          multiple
          onChange={(e) => void handleUpload(e.target.files)}
          className="hidden"
          id="knowledge-upload"
        />
        <label htmlFor="knowledge-upload" className="cursor-pointer">
          <div className="text-sm text-text-subtle">
            {uploading ? (
              <span>上传中...</span>
            ) : (
              <>
                <span className="text-ink hover:text-ink-deep">点击选择文件</span>
                <span> 或拖拽到此处</span>
              </>
            )}
          </div>
          <div className="mt-2xs text-xs text-text-muted">
            支持 PDF、DOCX、PPTX、XLSX、MD、TXT 等格式，单文件不超过 10MB
          </div>
        </label>
      </div>

      {error && <p className="mb-md text-sm text-danger">{error}</p>}

      {loading && <p className="text-sm text-text-muted">加载中...</p>}

      {!loading && files.length === 0 && !error && (
        <p className="text-text-muted">还没有上传文件。</p>
      )}

      {!loading && files.length > 0 && (
        <ul className="flex flex-col gap-xs">
          {files.map((file) => (
            <li
              key={file.id}
              className="rounded-lg border border-border-soft px-md py-sm"
            >
              <div className="flex items-start justify-between gap-sm">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-sm">
                    <span className="truncate text-sm font-medium text-text">
                      {file.filename}
                    </span>
                    {statusBadge(file.status)}
                  </div>
                  <div className="mt-2xs flex flex-wrap items-center gap-x-sm gap-y-1 text-xs text-text-subtle">
                    <span>{formatSize(file.size_bytes)}</span>
                    <span>·</span>
                    <span className="uppercase">{file.file_type}</span>
                    {file.status === "ready" && file.chunk_count > 0 && (
                      <>
                        <span>·</span>
                        <span>{file.chunk_count} 块</span>
                      </>
                    )}
                  </div>
                  {file.preview && (
                    <p className="mt-2xs line-clamp-2 text-xs text-text-muted">
                      {file.preview}
                    </p>
                  )}
                  {file.error_message && (
                    <p className="mt-2xs text-xs text-danger">{file.error_message}</p>
                  )}
                </div>
                <button
                  onClick={() => {
                    if (confirmDelete === file.id) {
                      void handleDelete(file.id);
                    } else {
                      setConfirmDelete(file.id);
                    }
                  }}
                  disabled={deleting === file.id}
                  className={`shrink-0 text-xs transition-colors ${
                    confirmDelete === file.id
                      ? "text-danger"
                      : "text-text-subtle hover:text-danger"
                  }`}
                >
                  {deleting === file.id
                    ? "删除中..."
                    : confirmDelete === file.id
                      ? "确定？"
                      : "删除"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
