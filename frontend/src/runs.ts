import { API, errorMessage } from './auth'

export type RunStatus = 'planning' | 'review' | 'researching' | 'complete' | 'failed' | 'interrupted'
export type Question = { question: string; rationale: string }
export type RunSummary = { thread_id: string; topic: string; title: string | null; status: RunStatus; created_at: string; updated_at: string }
export type RunDetail = RunSummary & { plan: Question[] | null; report: string | null }
export type Usage = { used_today: number; daily_limit: number | null; default_iterations: number }
export type RunList = { runs: RunSummary[]; usage: Usage }

/** Server limits on the plan editor (schemas.ResearchQuestion). */
export const QUESTION_MAX = 240

async function call(path: string, token: string, init: RequestInit = {}): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`${API}${path}`, { ...init, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...init.headers } })
  } catch {
    throw new Error('Cannot reach the server. Check that the API is running and try again.')
  }
  if (response.status === 401) throw new SessionExpired()
  if (!response.ok && response.status !== 204) throw new Error(await errorMessage(response))
  return response
}

export class SessionExpired extends Error {
  constructor() { super('Your session has expired. Please sign in again.') }
}

export async function listRuns(token: string): Promise<RunList> {
  return (await call('/research/runs', token)).json() as Promise<RunList>
}

export async function getRun(token: string, id: string): Promise<RunDetail> {
  return (await call(`/research/runs/${encodeURIComponent(id)}`, token)).json() as Promise<RunDetail>
}

export async function deleteRun(token: string, id: string): Promise<void> {
  await call(`/research/runs/${encodeURIComponent(id)}`, token, { method: 'DELETE' })
}

export async function askFollowUp(token: string, id: string, question: string): Promise<string> {
  const response = await call(`/research/runs/${encodeURIComponent(id)}/ask`, token, { method: 'POST', body: JSON.stringify({ question }) })
  return ((await response.json()) as { answer: string }).answer
}
