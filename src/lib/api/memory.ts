import { cachedUserId, http } from "./core";

export type MemoryStats = {
  status: string;
  count: number;
  path: string;
};

export type MemoryItem = {
  id: string;
  memory: string;
  created_at: string | null;
  categories: string[];
  confirmation_status: string;
};

// ── 活跃路由 ──

export function getMemoryContent(): Promise<{ content: string }> {
  return http<{ content: string }>(
    `/api/memory/me?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

export function saveMemoryContent(content: string): Promise<{ message: string; chars: number }> {
  return http<{ message: string; chars: number }>(
    `/api/memory/me?user_id=${encodeURIComponent(cachedUserId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
}

export function resetMemory(): Promise<{ deleted: number }> {
  return http<{ deleted: number }>(
    `/api/memory/reset?user_id=${encodeURIComponent(cachedUserId)}`,
    { method: "POST" },
  );
}

export function getMemoryStats(): Promise<MemoryStats> {
  return http<MemoryStats>(
    `/api/memory/stats?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

// ── AI Understanding ──

export type AboutYouResponse = {
  about_you: string;
  updated_at: string;
  patterns: Array<{
    insight: string;
    category: string;
    evidence_count: number;
    first_seen: string;
    updated_at: string;
  }>;
  intents: Array<{
    text: string;
    category: string;
    first_mentioned_at: string;
    last_mentioned_at: string;
    mention_count: number;
  }>;
  now_status: Record<string, string>;
  journey: Array<{
    id: string;
    type: string;
    content: string;
    date: string | null;
  }>;
};

export function getAIUnderstanding(): Promise<AboutYouResponse> {
  return http<AboutYouResponse>(
    `/api/memory/understanding?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

export function refreshAIUnderstanding(): Promise<{ message: string; chars: number }> {
  return http<{ message: string; chars: number }>(
    `/api/memory/understanding/refresh?user_id=${encodeURIComponent(cachedUserId)}`,
    { method: "POST" },
  );
}

export function correctAIUnderstanding(text: string): Promise<{ message: string; chars: number }> {
  return http<{ message: string; chars: number }>(
    `/api/memory/understanding/correct?user_id=${encodeURIComponent(cachedUserId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
  );
}

// ── 主动告诉 AI ──

export type TellType = "interest" | "value" | "relationship" | "moment" | "reflection";

export function tellAI(
  eventType: TellType,
  content: string,
): Promise<{ message: string; event_id?: string }> {
  return http<{ message: string; event_id?: string }>(
    `/api/memory/tell?user_id=${encodeURIComponent(cachedUserId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: eventType, content }),
    },
  );
}

// ── 退役路由兼容（返回空结果） ──

export function getMemoryList(): Promise<MemoryItem[]> {
  return http<MemoryItem[]>(
    `/api/memory/list?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

export function deleteMemory(_id: string): Promise<{ deleted: string }> {
  return Promise.reject(new Error("逐条删除已退役"));
}

export function updateMemory(
  _id: string,
  _content: string,
): Promise<{ updated: string }> {
  return Promise.reject(new Error("逐条编辑已退役"));
}

export function reviewMemory(
  _id: string,
  _status: "confirmed" | "rejected",
): Promise<{ reviewed: string; status: string }> {
  return Promise.reject(new Error("逐条审核已退役"));
}

// ── Observations（已退役） ──

export type Observation = {
  text: string;
  source_event_ids: string[];
  source_event_types: string[];
};

export type ObservationsResult = {
  observations: Observation[];
  generated_at: string | null;
  events_analyzed: number;
  period_days: number;
};

export function getObservations(_days: number = 7): Promise<ObservationsResult> {
  return Promise.resolve({
    observations: [],
    generated_at: null,
    events_analyzed: 0,
    period_days: 7,
  });
}
