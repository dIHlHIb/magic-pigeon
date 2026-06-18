import { io, type Socket } from 'socket.io-client'
import type { ClientToServerEvents, ServerToClientEvents } from '../types'

export type AgentSocket = Socket<ServerToClientEvents, ClientToServerEvents>

/**
 * Connect to the same origin. In dev the Vite proxy forwards /socket.io to the
 * Python backend; in production the backend serves this app directly — either
 * way the connection is same-origin, so no CORS dance is needed. The token
 * rides in the handshake auth payload, which the backend's connect handler
 * validates (rejecting the socket outright if it's wrong).
 */
export function createSocket(token: string): AgentSocket {
  return io({
    auth: { token },
    transports: ['polling'],
  })
}
