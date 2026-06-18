import { useCallback, useEffect, useState } from 'react'
import { fetchSessions } from '../lib/api'
import type { SessionMeta } from '../types'

/** Loads the session list for the sidebar and exposes a refresh trigger. */
export function useSessions() {
  const [sessions, setSessions] = useState<SessionMeta[]>([])

  const refresh = useCallback(() => {
    fetchSessions()
      .then(setSessions)
      .catch(() => {
        /* sidebar simply stays as-is on transient errors */
      })
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { sessions, refresh }
}
