// Minimal type stubs for @opencode-ai/sdk/v2 — only what the UI components need

import type { RGBA } from "@opentui/core"

export type GlobalEvent = {
  payload: {
    type: string
    properties: Record<string, unknown>
  }
}

export type Session = {
  id: string
  title?: string
  time: { created: number; updated: number }
  path: { cwd: string }
  cost?: number
  parentID?: string
  workspaceID?: string
}

export type SessionStatus = {
  status?: "running" | "idle" | "error"
  error?: string
}

export type Message = {
  id: string
  role: "user" | "assistant"
  sessionID: string
  parts: Part[]
  error?: { message: string }
  metadata?: {
    time?: { start: number; end?: number }
    tokens?: { input: number; output: number }
    cost?: number
    model?: { id: string; providerID: string }
  }
}

export type Part =
  | TextPart
  | FilePart
  | ToolInvocationPart
  | StepStartPart
  | SnapshotPart
  | ThinkingPart

export type TextPart = {
  id: string
  type: "text"
  text: string
  synthetic?: boolean
  metadata?: Record<string, unknown>
}

export type ThinkingPart = {
  id: string
  type: "thinking"
  thinking: string
}

export type FilePart = {
  id: string
  type: "file"
  file: string
  mime: string
  filename?: string
}

export type ToolInvocationPart = {
  id: string
  type: "tool-invocation"
  toolInvocation: {
    toolCallId: string
    toolName: string
    state: "call" | "partial-call" | "result"
    args?: unknown
    result?: unknown
  }
}

export type StepStartPart = {
  id: string
  type: "step-start"
}

export type SnapshotPart = {
  id: string
  type: "snapshot"
  snapshot: unknown
}

export type AssistantMessage = Message & { role: "assistant" }
export type UserMessage = Message & { role: "user" }

export type Agent = {
  name: string
  description?: string
  model?: { providerID: string; modelID: string }
  mode?: string
  hidden?: boolean
}

export type Provider = {
  id: string
  name: string
  env?: string[]
  models: Record<string, Model>
}

export type Model = {
  id: string
  name: string
  cost?: { input: number; output: number }
  context?: number
}

export type Config = {
  theme?: string
  model?: string
  experimental?: {
    disable_paste_summary?: boolean
  }
}

export type Command = {
  name: string
  description?: string
}

export type PermissionRequest = {
  id: string
  sessionID: string
  toolName: string
  input: unknown
}

export type QuestionRequest = {
  id: string
  sessionID: string
  message: string
  options?: string[]
}

export type LspStatus = {
  name: string
  status: string
}

export type McpStatus = {
  name: string
  status: string
  error?: string
}

export type McpResource = {
  uri: string
  name: string
}

export type FormatterStatus = {
  name: string
  status: string
}

export type VcsInfo = {
  branch?: string
  commit?: string
}

export type Todo = {
  id: string
  content: string
  status: "todo" | "in-progress" | "done"
}

export type Path = {
  home: string
  state: string
  config: string
  worktree: string
  directory: string
}

export type Workspace = {
  id: string
  name: string
  directory: string
}

export type ProviderListResponse = {
  providers: Provider[]
}

export type ProviderAuthMethod = {
  type: string
  label: string
}

export type AgentPart = {
  type: "agent"
  agent: string
}
