// Shared protocol + view-model types for the magic-pigeon frontend.
// These mirror the Flask + Socket.IO backend exactly — do not change the
// backend; this is the contract it already speaks.

// ── REST + usage payloads ──────────────────────────────────────────────────

/** Shape returned by AgentSession.usage_summary() and the `usage` event. */
export interface UsageSummary {
  model: string
  effort?: string
  plan_mode?: boolean
  tokens: {
    input: number
    output: number
    cache_write: number
    cache_read: number
  }
  total_tokens: number
  cost_usd: number
}

/** One model version within a tier (e.g. Opus 4.8). */
export interface ModelVersion {
  id: string
  label: string
  effort: boolean
}

/** A model tier with its selectable versions (from /api/config.modelTiers). */
export interface ModelTier {
  tier: string
  label: string
  versions: ModelVersion[]
}

/** One row in GET /api/sessions (sidebar list). */
export interface SessionMeta {
  filename: string
  title: string
  tags: string[]
  mtime: number
  updated: string
}

/** A normalized content block from a stored message. */
export type DisplayBlock =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; id: string; name: string; input: unknown }
  | { type: 'tool_result'; tool_use_id: string; content: string }

/** A normalized message from session history. */
export interface DisplayMessage {
  role: 'user' | 'assistant'
  blocks: DisplayBlock[]
  timestamp?: number
  node_id?: string
  sibling_count?: number
  sibling_index?: number
  frozen?: boolean
}

/** GET /api/sessions/<filename>. */
export interface SessionDetail {
  filename: string
  title: string
  tags: string[]
  messages: DisplayMessage[]
}

/** GET /api/config. */
export interface AppConfig {
  model: string
  pricing: Record<string, number>
  cwd: string
  modelTiers: ModelTier[]
  effortLevels: string[]
}

/** One saved memory (GET/POST/DELETE /api/memories). */
export interface Memory {
  content: string
  time: number
}

// ── Socket payloads ────────────────────────────────────────────────────────

/** The `session_info` event (also emitted on connect / new / load). */
export interface SessionInfo {
  filename: string
  session_id: string
  usage: UsageSummary
  title: string | null
  messages: DisplayMessage[]
}

export interface ServerToClientEvents {
  session_info: (d: SessionInfo) => void
  stream: (d: { chunk: string }) => void
  assistant_done: (d: { text: string }) => void
  tool_use: (d: { id: string; name: string; input: unknown }) => void
  tool_result: (d: { id: string; content: string }) => void
  dangerous: (d: { command: string }) => void
  plan_proposed: (d: { plan: string }) => void
  turn_start: (d: Record<string, never>) => void
  turn_done: (d: { usage: UsageSummary }) => void
  usage: (d: UsageSummary) => void
  title: (d: { title: string; filename: string }) => void
  compaction: (d: Record<string, never>) => void
  error: (d: { message: string }) => void
  branch_update: (d: { messages: DisplayMessage[] }) => void
}

export interface ClientToServerEvents {
  send_message: (d: { text: string }) => void
  stop: () => void
  new_session: () => void
  load_session: (d: { filename: string }) => void
  confirm_dangerous: (d: { allow: boolean }) => void
  regenerate: () => void
  edit_message: (d: { node_id: string; text: string }) => void
  switch_branch: (d: { node_id: string; direction: 'prev' | 'next' }) => void
  set_model: (d: { model: string; effort: string }) => void
  set_plan_mode: (d: { on: boolean }) => void
  respond_plan: (d: { approved: boolean; feedback: string }) => void
}

/**
 * The minimal structural slice of a socket.io client socket the app relies on.
 * The real Socket satisfies this, and tests can pass a hand-rolled fake.
 */
export interface MinimalSocket {
  connected: boolean
  on(event: string, listener: (...args: any[]) => void): unknown
  off(event: string, listener?: (...args: any[]) => void): unknown
  emit(event: string, ...args: any[]): unknown
  close(): void
}

// ── Chat view model (what the UI actually renders) ─────────────────────────
export type ChatItem =
  | { key: string; kind: 'user'; text: string; ts?: string; nodeId?: string; siblingCount?: number; siblingIndex?: number; frozen?: boolean }
  | { key: string; kind: 'assistant'; text: string; ts?: string; streaming?: boolean; nodeId?: string; siblingCount?: number; siblingIndex?: number; frozen?: boolean }
  | {
      key: string
      kind: 'tool'
      id: string
      name: string
      input: unknown
      result: string | null
      open: boolean
    }
  | { key: string; kind: 'danger'; command: string; resolved: boolean; allowed: boolean }
  | { key: string; kind: 'system'; text: string }
