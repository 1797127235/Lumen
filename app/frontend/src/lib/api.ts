import { getUserId } from "./userId";

export type SkillItem = { name: string; level: string; context?: string | null };

export type Profile = {
  nickname: string | null;
  school_name: string | null;
  school_level: string | null;
  major: string | null;
  grade: string | null;
  graduation_year: number | null;
  target_direction: string | null;
  target_company_level: string | null;
  current_skills: SkillItem[] | null;
  gpa: string | null;
  ranking: string | null;
  awards: string[] | null;
  // 履历与扩展信息
  bio: string | null;
  city: string | null;
  english_level: string | null;
  expected_salary: string | null;
  portfolio_links: Array<{ label: string; url: string }> | null;
  projects: Array<{
    title: string;
    tech_stack: string | null;
    role: string | null;
    period: string;
    description: string;
  }> | null;
  work_experience: Array<{
    company: string;
    role: string;
    period: string;
    description: string;
  }> | null;
};

export type ResumeUploadResponse = {
  profile: Profile;
  raw_text_preview: string;
};

export type GapSkill = {
  skill: string;
  priority: string;
};

export type JDDiagnoseResponse = {
  diagnosis_id?: string | null;
  jd_text?: string | null;
  jd_title: string;
  overall_score: number;
  summary: string;
  skill_gaps: GapSkill[];
  matched_skills: string[];
  strengths: string[];
  risks: string[];
  resume_tips: string[];
  action_plan: string[];
};

export type JDHistoryItem = {
  diagnosis_id: string;
  jd_title: string;
  overall_score: number;
  created_at: string;
};

export type TargetStatus =
  | "interested"
  | "applied"
  | "test"
  | "interview"
  | "offer"
  | "rejected"
  | "abandoned";

export type TargetCard = {
  target_id: string;
  company: string;
  title: string;
  status: TargetStatus;
  interview_round?: string | null;
  match_score?: number | null;
  agent_advice?: string | null;
  location?: string | null;
  created_at: string;
};

export type TargetDetail = TargetCard & {
  jd_text?: string | null;
  jd_url?: string | null;
  salary?: string | null;
  notes?: string | null;
  diagnosis?: JDDiagnoseResponse | null;
};

export type TargetCreatePayload = {
  company: string;
  title: string;
  location?: string | null;
  salary?: string | null;
  jd_text?: string | null;
  jd_url?: string | null;
  diagnosis_id?: string | null;
  notes?: string | null;
};

export type TargetUpdatePayload = {
  company?: string;
  title?: string;
  status?: TargetStatus;
  interview_round?: string | null;
  location?: string | null;
  salary?: string | null;
  notes?: string | null;
};

export type BoardStats = {
  total: number;
  avg_score: number;
  common_gaps: string[];
};

export type BoardResponse = {
  columns: Record<string, TargetCard[]>;
  stats: BoardStats;
};

export type ConversationSummary = {
  conversation_id: string;
  title: string | null;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
};

export type MessageItem = {
  message_id: string;
  role: "user" | "assistant" | string;
  content: string | null;
  intent: string | null;
  created_at: string;
};

export type SSEEvent =
  | { type: "token"; content: string; conversation_id: string }
  | { type: "done"; conversation_id: string }
  | { type: "error"; message: string };

function statusToZh(status: number): string {
  if (status === 401 || status === 403) return "让我先认识一下你.";
  if (status === 404) return "这条记录没找到.";
  if (status >= 500) return "我刚才走神了,你再问我一遍.";
  return "信号断了一下,你再发一次试试?";
}

async function http<T>(url: string, init?: RequestInit): Promise<T> {
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
  return res.json() as Promise<T>;
}

export function getProfile(): Promise<Profile> {
  return http<Profile>(`/api/profile/me?user_id=${encodeURIComponent(getUserId())}`);
}

export function patchProfile(patch: Partial<Profile>): Promise<Profile> {
  return http<Profile>(
    `/api/profile/me?user_id=${encodeURIComponent(getUserId())}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
}

export function resetProfile(): Promise<Profile> {
  return http<Profile>(
    `/api/profile/me?user_id=${encodeURIComponent(getUserId())}`,
    { method: "DELETE" },
  );
}

export function uploadResume(file: File): Promise<ResumeUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return http<ResumeUploadResponse>(
    `/api/profile/resume?user_id=${encodeURIComponent(getUserId())}`,
    { method: "POST", body: form },
  );
}

export function diagnoseJD(jd_text: string): Promise<JDDiagnoseResponse> {
  return http<JDDiagnoseResponse>(
    `/api/jd/diagnose?user_id=${encodeURIComponent(getUserId())}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd_text }),
    },
  );
}

export function getJDDiagnosis(diagnosisId: string): Promise<JDDiagnoseResponse> {
  return http<JDDiagnoseResponse>(
    `/api/jd/${encodeURIComponent(diagnosisId)}?user_id=${encodeURIComponent(getUserId())}`,
  );
}

export function getJDHistory(): Promise<{ items: JDHistoryItem[] }> {
  return http<{ items: JDHistoryItem[] }>(
    `/api/jd/history?user_id=${encodeURIComponent(getUserId())}`,
  );
}

export function deleteJDDiagnosis(diagnosisId: string): Promise<{ deleted: boolean }> {
  return http<{ deleted: boolean }>(
    `/api/jd/${encodeURIComponent(diagnosisId)}?user_id=${encodeURIComponent(getUserId())}`,
    { method: "DELETE" },
  );
}

export function getBoard(): Promise<BoardResponse> {
  return http<BoardResponse>(
    `/api/targets/board?user_id=${encodeURIComponent(getUserId())}`,
  );
}

export function getTarget(targetId: string): Promise<TargetDetail> {
  return http<TargetDetail>(
    `/api/targets/${encodeURIComponent(targetId)}?user_id=${encodeURIComponent(getUserId())}`,
  );
}

/** 排队后台重新生成行动建议；返回当前详情，需轮询 getTarget 直至 agent_advice 更新 */
export function regenerateTargetAdvice(targetId: string): Promise<TargetDetail> {
  return http<TargetDetail>(
    `/api/targets/${encodeURIComponent(targetId)}/regenerate-advice?user_id=${encodeURIComponent(getUserId())}`,
    { method: "POST" },
  );
}

export function createTarget(payload: TargetCreatePayload): Promise<TargetDetail> {
  return http<TargetDetail>(
    `/api/targets?user_id=${encodeURIComponent(getUserId())}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function updateTarget(
  targetId: string,
  patch: TargetUpdatePayload,
): Promise<TargetDetail> {
  return http<TargetDetail>(
    `/api/targets/${encodeURIComponent(targetId)}?user_id=${encodeURIComponent(getUserId())}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
}

export function deleteTarget(targetId: string): Promise<{ deleted: boolean }> {
  return http<{ deleted: boolean }>(
    `/api/targets/${encodeURIComponent(targetId)}?user_id=${encodeURIComponent(getUserId())}`,
    { method: "DELETE" },
  );
}

export function getChatHistory(limit = 20): Promise<ConversationSummary[]> {
  return http<ConversationSummary[]>(
    `/api/chat/history?user_id=${encodeURIComponent(getUserId())}&limit=${limit}`,
  );
}

export function getConversation(conversation_id: string): Promise<MessageItem[]> {
  return http<MessageItem[]>(
    `/api/chat/${encodeURIComponent(conversation_id)}?user_id=${encodeURIComponent(getUserId())}`,
  );
}

export type ChatStreamHandlers = {
  onToken: (delta: string, conversationId: string) => void;
  onDone: (conversationId: string) => void;
  onError: (message: string) => void;
  signal?: AbortSignal;
};

export async function chatStream(
  message: string,
  conversation_id: string | null,
  h: ChatStreamHandlers,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: h.signal,
      body: JSON.stringify({
        message,
        conversation_id: conversation_id ?? undefined,
        user_id: getUserId(),
      }),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    h.onError("信号断了一下,你再发一次试试?");
    return;
  }

  if (!res.ok || !res.body) {
    h.onError(statusToZh(res.status));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = raw.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        let evt: SSEEvent;
        try {
          evt = JSON.parse(payload) as SSEEvent;
        } catch {
          continue;
        }
        if (evt.type === "token") {
          h.onToken(evt.content, evt.conversation_id);
        } else if (evt.type === "done") {
          h.onDone(evt.conversation_id);
        } else if (evt.type === "error") {
          h.onError(evt.message);
        }
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      h.onError("我刚才走神了,你再问我一遍.");
    }
  }
}

// ── Config ──

export type Config = {
  dashscope_api_key: string;
  has_api_key: boolean;
};

export async function getConfig(): Promise<Config> {
  return http<Config>("/api/config");
}

export async function updateConfig(data: { dashscope_api_key?: string }): Promise<Config> {
  return http<Config>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}
