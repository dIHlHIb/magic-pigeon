import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChatItem } from '../types'
import { MessageRow } from './MessageRow'

interface Props {
  items: ChatItem[]
  onToggleTool: (key: string) => void
  onAllow: () => void
  onDeny: () => void
  onEdit?: (nodeId: string, text: string) => void
  onRegenerate?: () => void
  onSwitchBranch?: (nodeId: string, direction: 'prev' | 'next') => void
  /** Changes when a different chat is loaded; resets the scroll-stick so a newly
   * opened chat starts pinned to the bottom instead of inheriting the previous
   * chat's scroll position. */
  resetKey?: string | null
}

const NEAR_BOTTOM_PX = 120

/** Scrollable message stream; keeps the latest message in view. */
export function ChatView({ items, onToggleTool, onAllow, onDeny, onEdit, onRegenerate, onSwitchBranch, resetKey }: Props) {
  const chatRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const [showBtn, setShowBtn] = useState(false)
  const stickRef = useRef(true)

  // On chat switch, re-pin to the bottom so the new chat doesn't open at the
  // previous chat's stale scroll offset.
  useEffect(() => {
    stickRef.current = true
    setShowBtn(false)
  }, [resetKey])

  const isNearBottom = useCallback(() => {
    const el = chatRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
  }, [])

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [])

  const handleScroll = useCallback(() => {
    const near = isNearBottom()
    stickRef.current = near
    setShowBtn(!near)
  }, [isNearBottom])

  useEffect(() => {
    if (stickRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [items])

  return (
    <div className="chat" ref={chatRef} onScroll={handleScroll}>
      <div className="chat-inner">
        {items.length === 0 && (
          <div className="empty hero">
            Start a conversation. The agent shares memory, history, and tags with the terminal version.
          </div>
        )}
        {items.map((it) => (
          <MessageRow
            key={it.key}
            item={it}
            onToggleTool={onToggleTool}
            onAllow={onAllow}
            onDeny={onDeny}
            onEdit={onEdit}
            onRegenerate={onRegenerate}
            onSwitchBranch={onSwitchBranch}
          />
        ))}
        <div ref={bottomRef} />
      </div>
      {showBtn && (
        <button type="button" className="scroll-bottom-btn" onClick={scrollToBottom} aria-label="Scroll to bottom">
          ↓
        </button>
      )}
    </div>
  )
}
