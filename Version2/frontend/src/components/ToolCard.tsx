import type { ChatItem } from '../types'

type ToolItem = Extract<ChatItem, { kind: 'tool' }>

interface Props {
  item: ToolItem
  onToggle: () => void
}

/** Collapsible card for a tool call: name + args summary in the head, full
 *  arguments and result in the expandable body. */
export function ToolCard({ item, onToggle }: Props) {
  let argStr = ''
  try {
    argStr = JSON.stringify(item.input)
  } catch {
    argStr = ''
  }

  return (
    <div className={`tool-card ${item.open ? 'open' : ''}`}>
      <button type="button" className="tool-head" onClick={onToggle} aria-expanded={item.open}>
        <span className="tool-caret" aria-hidden="true">
          {item.open ? '▼' : '▶'}
        </span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-args">{argStr}</span>
        {item.result === null && <span className="tool-pending">running…</span>}
      </button>
      {item.open && (
        <div className="tool-body">
          <div className="tool-label">arguments</div>
          <pre>{JSON.stringify(item.input, null, 2)}</pre>
          <div className="tool-label">result</div>
          <pre>{item.result === null ? '(running…)' : item.result}</pre>
        </div>
      )}
    </div>
  )
}
