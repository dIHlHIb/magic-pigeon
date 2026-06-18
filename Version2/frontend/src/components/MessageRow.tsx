import { useCallback, useState } from 'react'
import type { ChatItem } from '../types'
import { ToolCard } from './ToolCard'
import { DangerCard } from './DangerCard'
import { MarkdownMessage } from './MarkdownMessage'

interface Props {
  item: ChatItem
  onToggleTool: (key: string) => void
  onAllow: () => void
  onDeny: () => void
  onEdit?: (nodeId: string, text: string) => void
  onRegenerate?: () => void
  onSwitchBranch?: (nodeId: string, direction: 'prev' | 'next') => void
}

export function MessageRow({ item, onToggleTool, onAllow, onDeny, onEdit, onRegenerate, onSwitchBranch }: Props) {
  // All hooks must run unconditionally, before any early return. Items keyed by
  // position (h0, h1, …) can change `kind` across chat switches; if a hook ran
  // only on some branches, React's hook count would change for a reused
  // instance and crash the whole tree ("rendered fewer hooks than expected").
  const [editing, setEditing] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const handleCopy = useCallback(() => {
    if (item.kind !== 'assistant' && item.kind !== 'user') return
    navigator.clipboard.writeText(item.text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [item])

  if (item.kind === 'tool') {
    return <ToolCard item={item} onToggle={() => onToggleTool(item.key)} />
  }
  if (item.kind === 'danger') {
    return <DangerCard item={item} onAllow={onAllow} onDeny={onDeny} />
  }
  if (item.kind === 'system') {
    return (
      <div className="msg system">
        <div className="bubble">{item.text}</div>
      </div>
    )
  }

  const hasBranch = (item.siblingCount ?? 0) > 1
  const who = item.kind === 'user' ? 'you' : 'magic-pigeon'
  const nodeId = item.nodeId

  const handleSave = () => {
    if (!editing?.trim()) { setEditing(null); return }
    if (!nodeId) {
      console.warn('[edit] no nodeId on item — branch_update may not have fired', item)
      setEditing(null)
      return
    }
    onEdit?.(nodeId, editing.trim())
    setEditing(null)
  }

  const handleRegenerate = () => {
    onRegenerate?.()
  }

  return (
    <div className={`msg ${item.kind}`}>
      <div className="who">
        {who}
        {item.ts ? ` · ${item.ts}` : ''}
      </div>
      <div className="bubble">
        {item.kind === 'user' && editing !== null ? (
          <div className="edit-area">
            <textarea
              className="edit-input"
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  handleSave()
                }
              }}
              autoFocus
            />
            <div className="edit-actions">
              <button className="btn-edit-save" onClick={handleSave}>
                Save &amp; Submit
              </button>
              <button className="btn-edit-cancel" onClick={() => setEditing(null)}>
                Cancel
              </button>
            </div>
          </div>
        ) : item.kind === 'assistant' ? (
          <>
            <MarkdownMessage text={item.text} />
            {item.streaming && <span className="cursor" aria-hidden="true" />}
          </>
        ) : (
          <div className="user-text">{item.text}</div>
        )}
      </div>
      {(item.kind === 'user' || item.kind === 'assistant') && !(item.kind === 'assistant' && item.streaming) && editing === null && (
        <div className="msg-actions">
          {hasBranch && nodeId && !item.frozen && (
            <span className="branch-nav">
              <button
                className="branch-arrow"
                disabled={(item.siblingIndex ?? 0) === 0}
                onClick={() => onSwitchBranch?.(nodeId, 'prev')}
              >
                &lt;
              </button>
              <span className="branch-idx">
                {(item.siblingIndex ?? 0) + 1}/{item.siblingCount}
              </span>
              <button
                className="branch-arrow"
                disabled={(item.siblingIndex ?? 0) >= (item.siblingCount ?? 1) - 1}
                onClick={() => onSwitchBranch?.(nodeId, 'next')}
              >
                &gt;
              </button>
            </span>
          )}
          {item.kind === 'assistant' && (
            <button className="action-btn" title={copied ? 'Copied!' : 'Copy'} onClick={handleCopy}>
              {copied ? '✓' : '⎘'}
            </button>
          )}
          {item.kind === 'user' && !item.frozen && (
            <>
              <button className="action-btn" title="Edit" onClick={() => setEditing(item.text)}>
                &#9998;
              </button>
              <button className="action-btn" title="Regenerate" onClick={handleRegenerate}>
                &#8635;
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
