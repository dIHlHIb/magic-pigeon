import type { ChatItem } from '../types'

type DangerItem = Extract<ChatItem, { kind: 'danger' }>

interface Props {
  item: DangerItem
  onAllow: () => void
  onDeny: () => void
}

/** Red warning card for a flagged dangerous command, with Allow/Deny actions
 *  while pending and a resolution note once answered. */
export function DangerCard({ item, onAllow, onDeny }: Props) {
  return (
    <div className="danger-card" role="alert">
      <div className="danger-head">⚠️ Dangerous command flagged</div>
      <pre className="danger-cmd">{item.command}</pre>
      {item.resolved ? (
        <div className="danger-hint">{item.allowed ? '→ Allowed by user' : '→ Denied by user'}</div>
      ) : (
        <div className="danger-actions">
          <button type="button" className="btn-deny" onClick={onDeny}>
            Deny
          </button>
          <button type="button" className="btn-allow" onClick={onAllow}>
            Allow
          </button>
        </div>
      )}
    </div>
  )
}
