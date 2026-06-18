import { useState } from 'react'
import type { SessionMeta } from '../types'
import { PigeonMark } from './PigeonMark'
import { renameSession, deleteSession } from '../lib/api'

interface Props {
  sessions: SessionMeta[]
  activeFile: string | null
  connected: boolean
  onNewChat: () => void
  onSelect: (filename: string) => void
  onRefresh: () => void
}

export function Sidebar({ sessions, activeFile, connected, onNewChat, onSelect, onRefresh }: Props) {
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')

  const filtered = search.trim()
    ? sessions.filter((s) =>
        s.title.toLowerCase().includes(search.toLowerCase()) ||
        s.tags?.some((t) => t.toLowerCase().includes(search.toLowerCase()))
      )
    : sessions

  const handleRename = (filename: string) => {
    const t = editTitle.trim()
    if (!t) { setEditing(null); return }
    renameSession(filename, t).then(() => { onRefresh(); setEditing(null) })
  }

  const handleDelete = (e: React.MouseEvent, filename: string) => {
    e.stopPropagation()
    if (!confirm('Delete this session?')) return
    deleteSession(filename).then(() => {
      onRefresh()
      if (filename === activeFile) onNewChat()
    })
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="logo">
          <PigeonMark /> magic-pigeon
        </span>
      </div>

      <button type="button" className="new-btn" onClick={onNewChat}>
        + New Chat
      </button>

      <div className="search-box">
        <input
          className="search-input"
          type="text"
          placeholder="Search sessions..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <nav className="session-list" aria-label="Sessions">
        {filtered.length === 0 && <div className="empty">{search ? 'No matches.' : 'No sessions yet.'}</div>}
        {filtered.map((s) => (
          <div
            key={s.filename}
            className={`session-item ${s.filename === activeFile ? 'active' : ''}`}
            onClick={() => onSelect(s.filename)}
          >
            {editing === s.filename ? (
              <input
                className="rename-input"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleRename(s.filename)
                  if (e.key === 'Escape') setEditing(null)
                }}
                onBlur={() => handleRename(s.filename)}
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span className="session-title">{s.title}</span>
            )}
            <span className="session-meta">
              {s.updated}
              <span className="session-actions">
                <button
                  className="session-action-btn"
                  title="Rename"
                  onClick={(e) => {
                    e.stopPropagation()
                    setEditing(s.filename)
                    setEditTitle(s.title)
                  }}
                >
                  &#9998;
                </button>
                <button
                  className="session-action-btn del"
                  title="Delete"
                  onClick={(e) => handleDelete(e, s.filename)}
                >
                  &times;
                </button>
              </span>
            </span>
          </div>
        ))}
      </nav>

      <div className="status-line">
        <span className={`conn-dot ${connected ? 'on' : 'off'}`} aria-hidden="true" />
        {connected ? 'connected' : 'disconnected'}
      </div>
    </aside>
  )
}
