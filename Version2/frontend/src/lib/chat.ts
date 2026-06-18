import type { ChatItem, DisplayMessage } from '../types'

// User messages are stored with a leading "[YYYY-MM-DD …] " timestamp the
// backend prepends; strip it for display.
const TS_RE = /^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*/

export function stripTimestamp(s: string): string {
  return typeof s === 'string' ? s.replace(TS_RE, '') : s
}

export function nowTime(): string {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Coerce a tool result (string from the live event / history) for display. */
export function stringifyResult(content: unknown): string {
  if (content === null || content === undefined) return ''
  return typeof content === 'string' ? content : String(content)
}

/**
 * Rebuild flat chat items from a loaded session's normalized messages. Each
 * tool_result block is folded into its matching tool_use card (matched by id).
 *
 * `keyPrefix` namespaces the generated React keys per load. Keys are positional
 * (h0, h1, …), so without a per-load prefix a key like "h1" would be reused when
 * switching chats and React would keep the old component instance — leaking its
 * local state (an open edit textarea, a "copied" flag) into the new chat. A
 * fresh prefix each load forces a clean remount.
 */
export function buildItemsFromHistory(messages: DisplayMessage[], keyPrefix = 'h'): ChatItem[] {
  const resultMap: Record<string, string> = {}
  for (const m of messages) {
    for (const b of m.blocks ?? []) {
      if (b.type === 'tool_result' && b.tool_use_id) {
        resultMap[b.tool_use_id] = b.content
      }
    }
  }

  const items: ChatItem[] = []
  let id = 0
  for (const m of messages) {
    let branchApplied = false
    for (const b of m.blocks ?? []) {
      if (b.type === 'text') {
        const txt = m.role === 'user' ? stripTimestamp(b.text) : b.text
        if (txt && txt.trim()) {
          const branch = !branchApplied && m.node_id
            ? { nodeId: m.node_id, siblingCount: m.sibling_count, siblingIndex: m.sibling_index, frozen: m.frozen }
            : {}
          branchApplied = true
          if (m.role === 'user') {
            items.push({ key: `${keyPrefix}${id++}`, kind: 'user', text: txt, ...branch })
          } else {
            items.push({ key: `${keyPrefix}${id++}`, kind: 'assistant', text: txt, ...branch })
          }
        }
      } else if (b.type === 'tool_use') {
        items.push({
          key: `${keyPrefix}${id++}`,
          kind: 'tool',
          id: b.id,
          name: b.name,
          input: b.input,
          result: resultMap[b.id] ?? null,
          open: false,
        })
      }
    }
  }
  return items
}

/** Resolve the most recent unresolved dangerous-command card (allow/deny). */
export function resolveLastDanger(items: ChatItem[], allowed: boolean): ChatItem[] {
  const copy = [...items]
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i]
    if (it.kind === 'danger' && !it.resolved) {
      copy[i] = { ...it, resolved: true, allowed }
      break
    }
  }
  return copy
}
