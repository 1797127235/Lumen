// API 共享基础 — userId、错误处理、HTTP 封装。

import { getUserId } from "../userId";

export const cachedUserId = getUserId();

export function statusToZh(status: number): string {
  if (status === 401 || status === 403) return "让我先认识一下你.";
  if (status === 404) return "这条记录没找到.";
  if (status >= 500) return "我刚才走神了,你再问我一遍.";
  return "信号断了一下,你再发一次试试?";
}

export async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = statusToZh(res.status);
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      // ignore — keep default
    }
    throw new Error(detail);
  }
  // 204 No Content or empty body → skip JSON parse
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  return JSON.parse(text) as T;
}
