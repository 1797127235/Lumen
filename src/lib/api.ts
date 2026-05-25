// API 客户端统一入口。
// 按领域拆分为子模块，此文件保持向后兼容重新导出全部符号。

// ── Core ──
export { cachedUserId, http, statusToZh } from "./api/core";

// ── Chat ──
export {
  chatStream,
  deleteConversation,
  getChatHistory,
  getConversation,
} from "./api/chat";
export type {
  ConversationSummary,
  MessageItem,
  SSEChatHandlers,
} from "./api/chat";

// ── Config ──
export { getConfig, getProviders, testConfig, updateConfig } from "./api/config";
export type { Config, ConfigTestResponse, ProviderCatalog } from "./api/config";

// ── Memory ──
export {
  correctAIUnderstanding,
  deleteMemory,
  getAIUnderstanding,
  getMemoryContent,
  getMemoryList,
  getMemoryStats,
  refreshAIUnderstanding,
  resetMemory,
  reviewMemory,
  saveMemoryContent,
  tellAI,
  updateMemory,
} from "./api/memory";
export type {
  AboutYouResponse,
  MemoryItem,
  MemoryStats,
  TellType,
} from "./api/memory";
