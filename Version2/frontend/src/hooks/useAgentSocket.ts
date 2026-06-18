import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChatItem, DisplayMessage, MinimalSocket, SessionInfo, UsageSummary } from '../types'
import { createSocket } from '../lib/socket'
import { getToken } from '../lib/auth'
import { buildItemsFromHistory, nowTime, resolveLastDanger, stringifyResult } from '../lib/chat'

const EMPTY_USAGE: UsageSummary = {
  model: '',
  tokens: { input: 0, output: 0, cache_write: 0, cache_read: 0 },
  total_tokens: 0,
  cost_usd: 0,
}

export interface UseAgentSocketOptions {
  /** Inject a socket (tests). When omitted a real socket.io socket is created. */
  socket?: MinimalSocket
  /** Token for the real socket handshake (defaults to getToken()). */
  token?: string
  /** Fired when the session list may have changed (session_info/title/turn_done). */
  onSessionsRefresh?: () => void
}

export interface AgentState {
  connected: boolean
  authError: boolean
  activeFile: string | null
  title: string
  usage: UsageSummary
  items: ChatItem[]
  busy: boolean
  dangerPending: boolean
  sendMessage: (text: string) => void
  stop: () => void
  newSession: () => void
  loadSession: (filename: string) => void
  confirmDangerous: (allow: boolean) => void
  toggleTool: (key: string) => void
  editMessage: (nodeId: string, text: string) => void
  regenerate: () => void
  switchBranch: (nodeId: string, direction: 'prev' | 'next') => void
  setModel: (model: string, effort: string) => void
}

/**
 * Owns the live agent connection and the chat view-model. Translates the
 * backend's Socket.IO event stream into a flat list of renderable chat items,
 * and exposes the client-side actions (send/stop/new/load/confirm).
 */
export function useAgentSocket(opts: UseAgentSocketOptions = {}): AgentState {
  const [connected, setConnected] = useState(false)
  const [authError, setAuthError] = useState(false)
  const [activeFile, setActiveFile] = useState<string | null>(null)
  const [title, setTitle] = useState('New chat')
  const [usage, setUsage] = useState<UsageSummary>(EMPTY_USAGE)
  const [items, setItems] = useState<ChatItem[]>([])
  const [busy, setBusy] = useState(false)
  const [dangerPending, setDangerPending] = useState(false)

  const socketRef = useRef<MinimalSocket | null>(null)
  const idRef = useRef(0)
  // Key of the assistant message currently being streamed (null = none open).
  const curAsst = useRef<string | null>(null)
  // Whether a turn is in flight. Guards against a late `stream` chunk arriving
  // after `turn_done` (network reordering / a trailing flush): without this it
  // would spawn a new assistant bubble that nothing ever finalizes — a ghost
  // bubble stuck with a blinking cursor forever.
  const turnActive = useRef(false)
  // Bumped on every history (re)load so generated item keys are unique per load,
  // forcing a clean remount and preventing local row state (edit buffer, copied
  // flag) from bleeding across chat switches. See buildItemsFromHistory.
  const loadGen = useRef(0)

  // Hold the latest refresh callback so the socket effect can stay mount-only.
  const refreshRef = useRef(opts.onSessionsRefresh)
  refreshRef.current = opts.onSessionsRefresh

  const nextKey = useCallback(() => `l${idRef.current++}`, [])

  useEffect(() => {
    // The real typed Socket is structurally narrower (literal event names); it
    // satisfies MinimalSocket at runtime, so bridge the type explicitly.
    const socket: MinimalSocket =
      opts.socket ?? (createSocket(opts.token ?? getToken()) as unknown as MinimalSocket)
    socketRef.current = socket
    const refresh = () => refreshRef.current?.()

    socket.on('connect', () => {
      setConnected(true)
      setAuthError(false)
    })
    socket.on('disconnect', () => setConnected(false))
    socket.on('connect_error', () => {
      setConnected(false)
      setAuthError(true)
    })

    socket.on('session_info', (d: SessionInfo) => {
      setActiveFile(d.filename)
      setTitle(d.title || 'New chat')
      if (d.usage) setUsage(d.usage)
      loadGen.current += 1
      setItems(d.messages?.length ? buildItemsFromHistory(d.messages, `h${loadGen.current}_`) : [])
      curAsst.current = null
      turnActive.current = false
      setBusy(false)
      setDangerPending(false)
      refresh()
    })

    socket.on('turn_start', () => {
      setBusy(true)
      turnActive.current = true
      curAsst.current = null
    })

    socket.on('stream', (d: { chunk: string }) => {
      if (curAsst.current === null) {
        // No open bubble and no active turn ⇒ this is a straggler chunk that
        // arrived after turn_done. Drop it instead of spawning a ghost bubble.
        if (!turnActive.current) return
        const key = nextKey()
        curAsst.current = key
        setItems((prev) => [
          ...prev,
          { key, kind: 'assistant', text: d.chunk, ts: nowTime(), streaming: true },
        ])
      } else {
        const key = curAsst.current
        setItems((prev) =>
          prev.map((it) =>
            it.key === key && it.kind === 'assistant' ? { ...it, text: it.text + d.chunk } : it,
          ),
        )
      }
    })

    socket.on('assistant_done', (d: { text: string }) => {
      const key = curAsst.current
      if (key !== null) {
        setItems((prev) =>
          prev.map((it) =>
            it.key === key && it.kind === 'assistant'
              ? { ...it, text: d.text || it.text, streaming: false }
              : it,
          ),
        )
      }
      curAsst.current = null
    })

    // A tool call or a dangerous prompt ends the current streaming text block.
    const closeStreaming = () => {
      const key = curAsst.current
      if (key !== null) {
        setItems((prev) =>
          prev.map((it) =>
            it.key === key && it.kind === 'assistant' ? { ...it, streaming: false } : it,
          ),
        )
        curAsst.current = null
      }
    }

    socket.on('tool_use', (d: { id: string; name: string; input: unknown }) => {
      closeStreaming()
      setItems((prev) => [
        ...prev,
        { key: nextKey(), kind: 'tool', id: d.id, name: d.name, input: d.input, result: null, open: false },
      ])
    })

    socket.on('tool_result', (d: { id: string; content: string }) => {
      setItems((prev) =>
        prev.map((it) =>
          it.kind === 'tool' && it.id === d.id ? { ...it, result: stringifyResult(d.content) } : it,
        ),
      )
    })

    socket.on('dangerous', (d: { command: string }) => {
      closeStreaming()
      setItems((prev) => [
        ...prev,
        { key: nextKey(), kind: 'danger', command: d.command, resolved: false, allowed: false },
      ])
      setDangerPending(true)
    })

    socket.on('usage', (u: UsageSummary) => setUsage(u))

    socket.on('compaction', () =>
      setItems((prev) => [...prev, { key: nextKey(), kind: 'system', text: 'context compressed' }]),
    )

    socket.on('title', (d: { title: string; filename: string }) => {
      setTitle(d.title)
      refresh()
    })

    socket.on('error', (d: { message: string }) =>
      setItems((prev) => [...prev, { key: nextKey(), kind: 'system', text: `⚠ ${d.message}` }]),
    )

    socket.on('turn_done', (d: { usage: UsageSummary }) => {
      setBusy(false)
      turnActive.current = false
      // Clear a stuck cursor if assistant_done never arrived (dropped event).
      closeStreaming()
      if (d?.usage) setUsage(d.usage)
      refresh()
    })

    socket.on('branch_update', (d: { messages: DisplayMessage[] }) => {
      const built = d.messages?.length ? buildItemsFromHistory(d.messages, `h${loadGen.current}_`) : []
      setItems((prev) => {
        // A normal turn ends with a branch_update carrying the same messages we
        // just streamed, now annotated with node_ids. Rebuilding from scratch
        // gives every row a new key and remounts the whole list, replaying each
        // message's entrance animation — a full-screen flash. When the structure
        // is unchanged, reuse the existing keys so React reconciles in place
        // (no remount, no flash) and just merges in the new node_ids.
        if (built.length === prev.length) {
          return built.map((it, i) => ({ ...it, key: prev[i].key }))
        }
        return built
      })
      curAsst.current = null
    })

    return () => socket.close()
    // Mount-only: opts.socket/token are read once by design; refresh is via ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nextKey])

  const sendMessage = useCallback(
    (text: string) => {
      const t = text.trim()
      if (!t || busy || dangerPending) return
      setItems((prev) => [...prev, { key: nextKey(), kind: 'user', text: t, ts: nowTime() }])
      socketRef.current?.emit('send_message', { text: t })
    },
    [busy, dangerPending, nextKey],
  )

  const stop = useCallback(() => {
    socketRef.current?.emit('stop')
    if (dangerPending) {
      setItems((prev) => resolveLastDanger(prev, false))
      setDangerPending(false)
    }
  }, [dangerPending])

  const confirmDangerous = useCallback((allow: boolean) => {
    socketRef.current?.emit('confirm_dangerous', { allow })
    setItems((prev) => resolveLastDanger(prev, allow))
    setDangerPending(false)
  }, [])

  const newSession = useCallback(() => {
    socketRef.current?.emit('new_session')
  }, [])

  const loadSession = useCallback(
    (filename: string) => {
      if (filename === activeFile) return
      socketRef.current?.emit('load_session', { filename })
    },
    [activeFile],
  )

  const toggleTool = useCallback((key: string) => {
    setItems((prev) =>
      prev.map((it) => (it.key === key && it.kind === 'tool' ? { ...it, open: !it.open } : it)),
    )
  }, [])

  const editMessage = useCallback((nodeId: string, text: string) => {
    socketRef.current?.emit('edit_message', { node_id: nodeId, text })
    // Optimistically show the edited text and drop the old continuation right
    // away, so the new response streams in below a clean, edited message instead
    // of appearing under the stale old version. If the server rejects the edit,
    // the end-of-turn branch_update restores the real branch.
    setItems((prev) => {
      const idx = prev.findIndex((it) => it.kind === 'user' && it.nodeId === nodeId)
      if (idx === -1) return prev
      const target = prev[idx]
      if (target.kind !== 'user') return prev
      return [...prev.slice(0, idx), { ...target, text }]
    })
  }, [])

  const regenerate = useCallback(() => {
    socketRef.current?.emit('regenerate')
  }, [])

  const switchBranch = useCallback((nodeId: string, direction: 'prev' | 'next') => {
    socketRef.current?.emit('switch_branch', { node_id: nodeId, direction })
  }, [])

  const setModel = useCallback((model: string, effort: string) => {
    socketRef.current?.emit('set_model', { model, effort })
  }, [])

  return {
    connected,
    authError,
    activeFile,
    title,
    usage,
    items,
    busy,
    dangerPending,
    sendMessage,
    stop,
    newSession,
    loadSession,
    confirmDangerous,
    toggleTool,
    editMessage,
    regenerate,
    switchBranch,
    setModel,
  }
}
