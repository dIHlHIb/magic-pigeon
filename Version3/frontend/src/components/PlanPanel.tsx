import { useState } from 'react'

interface Props {
  planMode: boolean
  planText: string | null
  busy: boolean
  onToggle: (on: boolean) => void
  onApprove: () => void
  onRequestChanges: (feedback: string) => void
}

/** Sits just above the composer. Holds the plan-mode toggle (also bound to
 *  Shift+Tab globally) and, when the agent has proposed a plan via present_plan,
 *  the approval card (approve → build, or send feedback to refine). While a plan
 *  is pending the turn is blocked server-side, so the user must resolve it. */
export function PlanPanel({ planMode, planText, busy, onToggle, onApprove, onRequestChanges }: Props) {
  const [feedback, setFeedback] = useState('')
  const pending = planText != null

  return (
    <div className={`plan-panel ${planMode ? 'on' : ''}`}>
      <div className="plan-bar">
        <button
          type="button"
          className={`plan-toggle ${planMode ? 'plan' : 'auto'}`}
          onClick={() => onToggle(!planMode)}
          disabled={busy || pending}
          title="Mode: AUTO = the agent acts directly. PLAN = it investigates (via explore sub-agents) and proposes a plan for your approval before editing. Toggle with Shift+Tab."
        >
          {planMode ? 'PLAN' : 'AUTO'}
        </button>
        <span className="plan-bar-hint">
          <kbd>Shift</kbd>+<kbd>Tab</kbd> to toggle · investigates &amp; plans, no edits until you approve
        </span>
      </div>

      {pending && (
        <div className="plan-card">
          <div className="plan-card-title">Proposed plan — review &amp; approve</div>
          <pre className="plan-card-body">{planText}</pre>
          <textarea
            className="plan-feedback"
            rows={2}
            value={feedback}
            placeholder="Optional: what to change (then Request changes)…"
            onChange={(e) => setFeedback(e.target.value)}
          />
          <div className="plan-card-actions">
            <button
              type="button"
              className="plan-approve"
              onClick={() => {
                setFeedback('')
                onApprove()
              }}
            >
              ✓ Approve &amp; build
            </button>
            <button
              type="button"
              className="plan-revise"
              onClick={() => {
                onRequestChanges(feedback)
                setFeedback('')
              }}
            >
              ↻ Request changes
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
