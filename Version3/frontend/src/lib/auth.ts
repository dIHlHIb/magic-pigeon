const STORAGE_KEY = 'mp_token'

/**
 * The backend prints an access URL carrying a token (?token=…). Read it from
 * the URL on first load and persist it for reloads. Falls back to the persisted
 * value, then to an empty string (which the app treats as "unauthorized").
 */
export function getToken(): string {
  const fromUrl = new URLSearchParams(window.location.search).get('token')
  if (fromUrl) {
    sessionStorage.setItem(STORAGE_KEY, fromUrl)
    return fromUrl
  }
  return sessionStorage.getItem(STORAGE_KEY) || ''
}
