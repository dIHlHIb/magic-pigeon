import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

export type ComposerMode = 'send' | 'stop'

interface Props {
  mode: ComposerMode
  connected: boolean
  focusKey?: string | null
  onSend: (text: string) => void
  onStop: () => void
}

/** Bottom input bar. While the agent runs (mode "stop") the send button becomes
 *  a stop button; Enter sends, Shift+Enter inserts a newline. */
export function Composer({ mode, connected, focusKey, onSend, onStop }: Props) {
  const [text, setText] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (focusKey != null) taRef.current?.focus()
  }, [focusKey])

  // Auto-grow the textarea up to a cap as content changes.
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    if (!text) {
      ta.style.height = ''
      return
    }
    ta.style.height = '0'
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [text])

  const submit = () => {
    const t = text.trim()
    if (!t) return
    onSend(t)
    setText('')
  }

  const handleClick = () => {
    if (mode === 'stop') onStop()
    else submit()
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (mode !== 'stop') submit()
    }
  }

  const disabled = mode === 'stop' ? false : !connected || !text.trim()

  return (
    <div className="composer">
      <div className="composer-inner">
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          aria-label="Message input"
          placeholder="Message magic-pigeon…  (Enter to send, Shift+Enter for newline)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className={`send-btn ${mode === 'stop' ? 'stop' : ''}`}
          onClick={handleClick}
          disabled={disabled}
        >
          {mode === 'stop' ? '■ Stop' : 'Send'}
        </button>
      </div>
    </div>
  )
}
