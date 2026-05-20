import { cachedUserId, http, statusToZh } from "./core";

export type ConversationSummary = {
  conversation_id: string;
  title: string | null;
  message_count: number;
  total_tokens: number;
  last_message_at: string | null;
  created_at: string;
};

export type MessageItem = {
  message_id: string;
  role: "user" | "assistant" | string;
  content: string | null;
  intent: string | null;
  tokens_used: number | null;
  created_at: string;
};

export function getChatHistory(limit = 20): Promise<ConversationSummary[]> {
  return http<ConversationSummary[]>(
    `/api/chat/history?user_id=${encodeURIComponent(cachedUserId)}&limit=${limit}`,
  );
}

export function getConversation(conversation_id: string): Promise<MessageItem[]> {
  return http<MessageItem[]>(
    `/api/chat/${encodeURIComponent(conversation_id)}?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

export function deleteConversation(conversation_id: string): Promise<{ deleted: boolean }> {
  return http<{ deleted: boolean }>(
    `/api/chat/${encodeURIComponent(conversation_id)}?user_id=${encodeURIComponent(cachedUserId)}`,
    { method: "DELETE" },
  );
}

// ── Chat SSE ──

export type SSEChatHandlers = {
  onToken: (delta: string, conversationId: string) => void;
  onDone: (conversationId: string, usage?: { input: number; output: number }) => void;
  onTrace: (kind: "call" | "result", tool: string, content: string) => void;
  onError: (message: string) => void;
  signal?: AbortSignal;
};

export async function chatStream(
  message: string,
  conversation_id: string | null,
  h: SSEChatHandlers,
  attachments?: string[],
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: h.signal,
    body: JSON.stringify({
      message,
      conversation_id: conversation_id ?? undefined,
      user_id: cachedUserId,
      attachments: attachments ?? [],
    }),
  });

  if (!res.ok || !res.body) {
    h.onError(statusToZh(res.status));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  // thinking 状态：0=未开始, 1=进行中(已发<think>), 2=已结束(已发</think>)
  let thinkingState: 0 | 1 | 2 = 0;

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

      let ev: Record<string, unknown>;
      try {
        ev = JSON.parse(payload);
      } catch {
        continue;
      }

      switch (ev.type) {
        case "thinking": {
          const content = String(ev.content ?? "");
          if (!content) break;
          // 第一次收到 thinking，先发送 <think> 标签
          if (thinkingState === 0) {
            h.onToken("<think>\n", String(ev.conversation_id ?? ""));
            thinkingState = 1;
          }
          h.onToken(content, String(ev.conversation_id ?? ""));
          break;
        }
        case "token": {
          const content = String(ev.content ?? "");
          // 如果之前有 thinking 内容未关闭，先关闭 </think>
          if (thinkingState === 1) {
            h.onToken("\n</think>\n", String(ev.conversation_id ?? ""));
            thinkingState = 2;
          }
          h.onToken(content, String(ev.conversation_id ?? ""));
          break;
        }
        case "trace":
          h.onTrace(
            String(ev.kind ?? "call") as "call" | "result",
            String(ev.tool ?? ""),
            String(ev.content ?? ""),
          );
          break;
        case "done": {
          // 如果 thinking 未关闭，先关闭它
          if (thinkingState === 1) {
            h.onToken("\n</think>", String(ev.conversation_id ?? ""));
            thinkingState = 2;
          }
          h.onDone(
            String(ev.conversation_id ?? ""),
            ev.usage as { input: number; output: number } | undefined,
          );
          break;
        }
        case "error": {
          // 如果 thinking 未关闭，先关闭它
          if (thinkingState === 1) {
            h.onToken("\n</think>", String(ev.conversation_id ?? ""));
            thinkingState = 2;
          }
          h.onError(String(ev.message ?? "未知错误"));
          break;
        }
      }
    }
  }
}
