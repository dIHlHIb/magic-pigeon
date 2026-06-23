import { getToken } from './auth'
import type { AppConfig, Memory, SessionDetail, SessionMeta } from '../types'

// Every REST call carries the access token in the X-Auth-Token header (the
// backend's @before_request gate requires it for anything under /api).
function authFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  const headers = new Headers(opts.headers)
  headers.set('X-Auth-Token', getToken())
  return fetch(url, { ...opts, headers })
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`Request failed: ${res.status}`)
  return (await res.json()) as T
}

export function fetchConfig(): Promise<AppConfig> {
  return authFetch('/api/config').then((r) => json<AppConfig>(r))
}

export function fetchSessions(): Promise<SessionMeta[]> {
  return authFetch('/api/sessions').then((r) => json<SessionMeta[]>(r))
}

export function fetchSession(filename: string): Promise<SessionDetail> {
  return authFetch(`/api/sessions/${encodeURIComponent(filename)}`).then((r) => json<SessionDetail>(r))
}

export function fetchMemories(): Promise<Memory[]> {
  return authFetch('/api/memories').then((r) => json<Memory[]>(r))
}

export function addMemory(content: string): Promise<Memory[]> {
  return authFetch('/api/memories', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  }).then((r) => json<Memory[]>(r))
}

export function deleteMemory(index: number): Promise<Memory[]> {
  return authFetch(`/api/memories/${index}`, { method: 'DELETE' }).then((r) => json<Memory[]>(r))
}

export function searchHistory(q: string): Promise<string> {
  return authFetch(`/api/search?q=${encodeURIComponent(q)}`)
    .then((r) => json<{ result: string }>(r))
    .then((d) => d.result)
}

export function renameSession(filename: string, title: string): Promise<void> {
  return authFetch(`/api/sessions/${encodeURIComponent(filename)}/title`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }).then(() => undefined)
}

export function deleteSession(filename: string): Promise<void> {
  return authFetch(`/api/sessions/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  }).then(() => undefined)
}
