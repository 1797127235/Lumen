import { cachedUserId, http } from "./core";

export type MemoryStats = {
  status: string;
  count: number;
};

export type MemoryItem = {
  id: string;
  memory: string;
  created_at: string | null;
  categories: string[];
  confirmation_status: string;
};

export function getMemoryContent(): Promise<{ content: string }> {
  return http<{ content: string }>(
    `/api/memory/me?user_id=${encodeURIComponent(cachedUserId)}`,
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

export function getMemoryList(): Promise<MemoryItem[]> {
  return http<MemoryItem[]>(
    `/api/memory/list?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

export function deleteMemory(id: string): Promise<{ deleted: string }> {
  return http<{ deleted: string }>(
    `/api/memory/${encodeURIComponent(id)}?user_id=${encodeURIComponent(cachedUserId)}`,
    { method: "DELETE" },
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

export function updateMemory(
  id: string,
  content: string,
): Promise<{ updated: string }> {
  return http<{ updated: string }>(
    `/api/memory/${encodeURIComponent(id)}?user_id=${encodeURIComponent(cachedUserId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
}

export function reviewMemory(
  id: string,
  status: "confirmed" | "rejected",
): Promise<{ reviewed: string; status: string }> {
  return http<{ reviewed: string; status: string }>(
    `/api/memory/${encodeURIComponent(id)}/review?user_id=${encodeURIComponent(cachedUserId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
}

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

// ── Observations（顶部观察条）──

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

export function getObservations(days: number = 7): Promise<ObservationsResult> {
  return http<ObservationsResult>(
    `/api/memory/observations?days=${days}&user_id=${encodeURIComponent(cachedUserId)}`,
  );
}
