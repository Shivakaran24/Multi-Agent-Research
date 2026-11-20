import { API, errorMessage } from './auth'
import { formatTokens } from './format'

export { formatTokens }

export type RunStatus = 'planning' | 'review' | 'researching' | 'complete' | 'failed' | 'interrupted'
export type Question = { question: string; rationale: string }
export type RunSummary = { thread_id: string; topic: string; title: string | null; status: RunStatus; created_at: string; updated_at: string; feedback?: number | null; share_token?: string | null; tokens_used?: number; schedule_id?: string | null; tags?: string[] }
export type RunDetail = RunSummary & { plan: Question[] | null; report: string | null }
export type Usage = { used_today: number; daily_limit: number | null; default_iterations: number; tokens_today?: number; token_limit?: number | null }
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

export async function setFeedback(token: string, id: string, value: 1 | -1 | 0): Promise<void> {
  await call(`/research/runs/${encodeURIComponent(id)}/feedback`, token, { method: 'PUT', body: JSON.stringify({ value }) })
}

export async function createShare(token: string, id: string): Promise<string> {
  const response = await call(`/research/runs/${encodeURIComponent(id)}/share`, token, { method: 'POST' })
  return ((await response.json()) as { share_token: string }).share_token
}

export async function revokeShare(token: string, id: string): Promise<void> {
  await call(`/research/runs/${encodeURIComponent(id)}/share`, token, { method: 'DELETE' })
}

export const shareUrl = (shareToken: string) => `${window.location.origin}${window.location.pathname}#/shared?token=${shareToken}`


export type Frequency = 'daily' | 'weekly' | 'monthly'
export type Schedule = {
  id: string; topic: string; frequency: Frequency; depth: number | null; enabled: boolean
  next_run_at: string; last_run_at: string | null; last_thread_id: string | null; last_status: string | null; created_at: string
}

export async function listSchedules(token: string): Promise<{ schedules: Schedule[]; limit: number }> {
  return (await call('/research/schedules', token)).json() as Promise<{ schedules: Schedule[]; limit: number }>
}

export async function createSchedule(token: string, topic: string, frequency: Frequency, options?: object): Promise<Schedule> {
  return (await call('/research/schedules', token, { method: 'POST', body: JSON.stringify({ topic, frequency, ...(options ? { options } : {}) }) })).json() as Promise<Schedule>
}

export type Template = { id: string; name: string; topic: string; options: Record<string, unknown> }

export async function listTemplates(token: string): Promise<Template[]> {
  return ((await (await call('/research/templates', token)).json()) as { templates: Template[] }).templates
}

export async function saveTemplate(token: string, name: string, topic: string, options: object): Promise<Template> {
  return (await call('/research/templates', token, { method: 'POST', body: JSON.stringify({ name, topic, options }) })).json() as Promise<Template>
}

export async function deleteTemplate(token: string, id: string): Promise<void> {
  await call(`/research/templates/${encodeURIComponent(id)}`, token, { method: 'DELETE' })
}

export type UploadedPdf = { name: string; chars: number; text: string }

export async function uploadPdf(token: string, file: File): Promise<UploadedPdf> {
  const response = await call(`/research/uploads/pdf?name=${encodeURIComponent(file.name)}`, token, { method: 'POST', body: file, headers: { 'Content-Type': 'application/pdf' } })
  return response.json() as Promise<UploadedPdf>
}

export async function setScheduleEnabled(token: string, id: string, enabled: boolean): Promise<Schedule> {
  return (await call(`/research/schedules/${encodeURIComponent(id)}`, token, { method: 'PATCH', body: JSON.stringify({ enabled }) })).json() as Promise<Schedule>
}

export async function deleteSchedule(token: string, id: string): Promise<void> {
  await call(`/research/schedules/${encodeURIComponent(id)}`, token, { method: 'DELETE' })
}

export async function runScheduleNow(token: string, id: string): Promise<string> {
  return ((await (await call(`/research/schedules/${encodeURIComponent(id)}/run`, token, { method: 'POST' })).json()) as { detail: string }).detail
}

export async function setTags(token: string, id: string, tags: string[]): Promise<string[]> {
  return ((await (await call(`/research/runs/${encodeURIComponent(id)}/tags`, token, { method: 'PUT', body: JSON.stringify({ tags }) })).json()) as { tags: string[] }).tags
}

export async function emailBrief(token: string, id: string, to?: string): Promise<string> {
  return ((await (await call(`/research/runs/${encodeURIComponent(id)}/email`, token, { method: 'POST', body: JSON.stringify(to ? { to } : {}) })).json()) as { detail: string }).detail
}

export type AdminStats = {
  users: number; new_users_7d: number; runs_total: number; runs_24h: number; runs_7d: number
  runs_by_status: Record<string, number>; tokens_total: number; tokens_24h: number
  feedback_up: number; feedback_down: number; schedules: number; schedules_active: number
  recent_failures: { topic: string; status: string; created_at: string; user: string }[]
  top_users: { email: string; runs: number; tokens: number }[]
}

export async function getAdminStats(token: string): Promise<AdminStats> {
  return (await call('/research/admin/stats', token)).json() as Promise<AdminStats>
}
