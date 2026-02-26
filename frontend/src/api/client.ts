/**
 * Authenticated API client. Uses Bearer token, retries on 401 via refresh.
 */

export type OnAuthFailure = () => void
export type OnTokenRefreshed = (token: string) => void

let authFailureCallback: OnAuthFailure | null = null
let tokenRefreshedCallback: OnTokenRefreshed | null = null

export function setAuthFailureCallback(cb: OnAuthFailure | null) {
  authFailureCallback = cb
}

export function setTokenRefreshedCallback(cb: OnTokenRefreshed | null) {
  tokenRefreshedCallback = cb
}

async function refreshToken(): Promise<string | null> {
  const res = await fetch('/api/refresh', {
    method: 'POST',
    credentials: 'include',
  })
  if (!res.ok) return null
  const data = await res.json()
  const token = data.access_token ?? null
  if (token) tokenRefreshedCallback?.(token)
  return token
}

export interface FetchOptions extends RequestInit {
  token?: string | null
}

export async function apiFetch(
  url: string,
  options: FetchOptions = {}
): Promise<Response> {
  const { token, ...rest } = options
  const headers = new Headers(rest.headers ?? {})
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (!headers.has('Content-Type') && rest.body && typeof rest.body === 'string') {
    headers.set('Content-Type', 'application/json')
  }

  let res = await fetch(url, { ...rest, headers, credentials: 'include' })

  if (res.status === 401) {
    const newToken = await refreshToken()
    if (newToken) {
      headers.set('Authorization', `Bearer ${newToken}`)
      res = await fetch(url, { ...rest, headers, credentials: 'include' })
    }
    if (res.status === 401) {
      authFailureCallback?.()
      return res
    }
  }

  return res
}
