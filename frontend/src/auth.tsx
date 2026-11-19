import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export const API = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export type User = { id: string; email: string; full_name: string; created_at: string }
type AuthResponse = { access_token: string; expires_in: number; user: User }
export type Session = { token: string; user: User; expiresAt: number }
export type ServerAuthConfig = { registration_open: boolean; password_reset_available: boolean }

type AuthContextValue = {
  session: Session | null
  /** False until any saved session has been checked with the server. */
  ready: boolean
  /** Why the user was signed out (e.g. an expired session), shown on the login page. */
  notice: string
  login: (email: string, password: string) => Promise<void>
  register: (fullName: string, email: string, password: string) => Promise<void>
  logout: (notice?: string) => void
  /** Changes the password; this device stays signed in, every other device is signed out. */
  changePassword: (current: string, next: string) => Promise<void>
  deleteAccount: (password: string) => Promise<void>
}

const STORAGE_KEY = 'fieldwork.session'
const AuthContext = createContext<AuthContextValue | null>(null)

function readSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const saved = JSON.parse(raw) as Session
    if (!saved?.token || !saved.user?.id || saved.expiresAt <= Date.now()) return null
    return saved
  } catch {
    return null
  }
}

function writeSession(session: Session | null) {
  try {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* Storage can be blocked (private mode); the session then lasts until reload. */
  }
}

/** Turns a failed API response into one readable sentence. */
export async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({}))
  const detail = body?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg).replace(/^Value error, /, '')
  return `Request failed (${response.status})`
}

async function send(path: string, payload: object, token?: string): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`${API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new Error('Cannot reach the server. Check that the API is running and try again.')
  }
  if (!response.ok) throw new Error(await errorMessage(response))
  return response
}

const postAuth = async (path: string, payload: object, token?: string) => (await send(path, payload, token)).json() as Promise<AuthResponse>

/** Sends the reset email (the server answers identically whether or not the account exists). */
export async function forgotPassword(email: string): Promise<string> {
  return ((await (await send('/auth/forgot-password', { email })).json()) as { detail: string }).detail
}

export async function resetPassword(token: string, newPassword: string): Promise<string> {
  return ((await (await send('/auth/reset-password', { token, new_password: newPassword })).json()) as { detail: string }).detail
}

/** What this server allows. Older servers without the endpoint get sensible defaults. */
export async function fetchAuthConfig(): Promise<ServerAuthConfig> {
  try {
    const response = await fetch(`${API}/auth/config`)
    if (response.ok) return (await response.json()) as ServerAuthConfig
  } catch { /* fall through */ }
  return { registration_open: true, password_reset_available: false }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(readSession)
  const [ready, setReady] = useState(() => readSession() === null)
  const [notice, setNotice] = useState('')

  const logout = useCallback((message = '') => {
    writeSession(null)
    setSession(null)
    setNotice(message)
  }, [])

  const start = useCallback((auth: AuthResponse) => {
    const next: Session = { token: auth.access_token, user: auth.user, expiresAt: Date.now() + auth.expires_in * 1000 }
    writeSession(next)
    setNotice('')
    setSession(next)
  }, [])

  // Confirm a saved session is still accepted (secret rotated, password changed elsewhere, account removed).
  useEffect(() => {
    const saved = readSession()
    if (!saved) return
    let cancelled = false
    fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${saved.token}` } })
      .then(async response => {
        if (cancelled) return
        if (response.status === 401) logout('Your session has expired. Please sign in again.')
        else if (response.ok) setSession({ ...saved, user: (await response.json()) as User })
      })
      .catch(() => { /* Server unreachable: keep the session; requests will report the problem. */ })
      .finally(() => { if (!cancelled) setReady(true) })
    return () => { cancelled = true }
  }, [logout])

  // Sign out automatically at expiry.
  useEffect(() => {
    if (!session) return
    const wait = Math.min(session.expiresAt - Date.now(), 2 ** 31 - 1)
    const timer = window.setTimeout(() => logout('Your session has expired. Please sign in again.'), Math.max(wait, 0))
    return () => window.clearTimeout(timer)
  }, [session, logout])

  const value = useMemo<AuthContextValue>(() => ({
    session,
    ready,
    notice,
    logout,
    login: async (email, password) => start(await postAuth('/auth/login', { email, password })),
    register: async (full_name, email, password) => start(await postAuth('/auth/register', { full_name, email, password })),
    changePassword: async (current_password, new_password) => {
      if (!session) throw new Error('Please sign in again.')
      start(await postAuth('/auth/change-password', { current_password, new_password }, session.token))
    },
    deleteAccount: async password => {
      if (!session) throw new Error('Please sign in again.')
      await send('/auth/delete-account', { password }, session.token)
      logout('Your account and all its research have been deleted.')
    },
  }), [session, ready, notice, logout, start])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>.')
  return value
}
