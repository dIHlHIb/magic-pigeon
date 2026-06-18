import { useEffect, useMemo, useState } from 'react'
import { getToken } from './lib/auth'
import { fetchConfig } from './lib/api'
import { useAgentSocket } from './hooks/useAgentSocket'
import { useSessions } from './hooks/useSessions'
import { Sidebar } from './components/Sidebar'
import { Topbar } from './components/Topbar'
import { ChatView } from './components/ChatView'
import { Composer } from './components/Composer'
import type { ModelTier } from './types'

export default function App() {
  const token = useMemo(() => getToken(), [])
  const { sessions, refresh } = useSessions()
  const agent = useAgentSocket({ token, onSessionsRefresh: refresh })

  // The model tier→version cascade + effort levels come from the backend once.
  const [modelTiers, setModelTiers] = useState<ModelTier[]>([])
  const [effortLevels, setEffortLevels] = useState<string[]>([])
  useEffect(() => {
    if (!token) return
    fetchConfig()
      .then((c) => {
        setModelTiers(c.modelTiers ?? [])
        setEffortLevels(c.effortLevels ?? [])
      })
      .catch(() => {})
  }, [token])

  if (!token || agent.authError) {
    return (
      <div className="app unauth">
        <div className="unauth-box">
          <div className="unauth-title">🔒 Unauthorized</div>
          <p>
            {!token
              ? 'No access token. Open the URL printed in the terminal — it includes ?token=…'
              : 'Access token rejected. Re-open the URL printed in the terminal (the token rotates each run unless MAGIC_PIGEON_TOKEN is set).'}
          </p>
        </div>
      </div>
    )
  }

  // A pending dangerous prompt means the turn is still running and blocked on the
  // user, so the composer stays a Stop button (which also denies); the Allow/Deny
  // actions live on the card itself.
  const composerMode = agent.busy ? 'stop' : 'send'

  return (
    <div className="app">
      <Sidebar
        sessions={sessions}
        activeFile={agent.activeFile}
        connected={agent.connected}
        onNewChat={agent.newSession}
        onSelect={agent.loadSession}
        onRefresh={refresh}
      />
      <main className="main">
        <Topbar
          title={agent.title}
          usage={agent.usage}
          modelTiers={modelTiers}
          effortLevels={effortLevels}
          busy={agent.busy}
          onSetModel={agent.setModel}
        />
        <ChatView
          items={agent.items}
          onToggleTool={agent.toggleTool}
          onAllow={() => agent.confirmDangerous(true)}
          onDeny={() => agent.confirmDangerous(false)}
          onEdit={agent.editMessage}
          onRegenerate={agent.regenerate}
          onSwitchBranch={agent.switchBranch}
          resetKey={agent.activeFile}
        />
        <Composer
          mode={composerMode}
          connected={agent.connected}
          focusKey={agent.activeFile}
          onSend={agent.sendMessage}
          onStop={agent.stop}
        />
      </main>
    </div>
  )
}
