import type { ModelTier, UsageSummary } from '../types'

interface Props {
  title: string
  usage: UsageSummary
  modelTiers: ModelTier[]
  effortLevels: string[]
  busy: boolean
  onSetModel: (model: string, effort: string) => void
}

/**
 * Header above the chat. Three cascading pickers: model tier (Opus/Sonnet/Haiku)
 * → version (e.g. Opus 4.8/4.7/4.6) → effort (only for versions that support it).
 */
export function Topbar({ title, usage, modelTiers, effortLevels, busy, onSetModel }: Props) {
  const current = usage.model
  const effort = usage.effort || 'high'

  const currentTier = modelTiers.find((t) => t.versions.some((v) => v.id === current))
  const currentVersion = currentTier?.versions.find((v) => v.id === current)
  const supportsEffort = currentVersion?.effort ?? false

  // Switching tier jumps to that tier's newest version (first in the list).
  const onTierChange = (tierName: string) => {
    const tier = modelTiers.find((t) => t.tier === tierName)
    if (tier && tier.versions[0]) onSetModel(tier.versions[0].id, effort)
  }

  return (
    <header className="topbar">
      <span className="chat-title">{title}</span>
      <div className="meta">
        {modelTiers.length > 0 && (
          <>
            <select
              className="model-select"
              value={currentTier?.tier ?? ''}
              disabled={busy}
              title="Model"
              onChange={(e) => onTierChange(e.target.value)}
            >
              {modelTiers.map((t) => (
                <option key={t.tier} value={t.tier}>
                  {t.label}
                </option>
              ))}
            </select>
            {currentTier && (
              <select
                className="model-select"
                value={current}
                disabled={busy}
                title="Version"
                onChange={(e) => onSetModel(e.target.value, effort)}
              >
                {currentTier.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            )}
            {supportsEffort && effortLevels.length > 0 && (
              <select
                className="model-select"
                value={effort}
                disabled={busy}
                title="Effort — lower is faster and cheaper"
                onChange={(e) => onSetModel(current, e.target.value)}
              >
                {effortLevels.map((lvl) => (
                  <option key={lvl} value={lvl}>
                    effort: {lvl}
                  </option>
                ))}
              </select>
            )}
          </>
        )}
        <span>
          tokens <b>{(usage.total_tokens || 0).toLocaleString()}</b>
        </span>
        <span>
          cost <b>${(usage.cost_usd || 0).toFixed(4)}</b>
        </span>
      </div>
    </header>
  )
}
