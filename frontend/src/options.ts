/** Per-run research choices: what the composer collects and what the API receives. Kept free of React so it can be unit-tested. */

export type Style = 'brief' | 'executive' | 'pros_cons' | 'comparison'
export type Audience = 'general' | 'executive' | 'technical' | 'student'
export type Length = 'short' | 'standard' | 'long'
export type TimeRange = '' | 'month' | 'year'

export type Options = {
  style: Style
  audience: Audience
  length: Length
  language: string
  timeRange: TimeRange
  verify: boolean
  links: string // one link per line
  notes: string
  compare: boolean
  compareWith: string
}

export const DEFAULT_OPTIONS: Options = {
  style: 'brief', audience: 'general', length: 'standard', language: 'English', timeRange: '',
  verify: true, links: '', notes: '', compare: false, compareWith: '',
}

export const MAX_LINKS = 5

export function parseLinks(text: string): string[] {
  const seen: string[] = []
  for (const line of text.split(/\s+/)) {
    const url = line.trim()
    if (url && !seen.includes(url)) seen.push(url)
  }
  return seen
}

/** Problems the user can fix before sending, or '' when the options are fine. */
export function optionsProblem(o: Options): string {
  const links = parseLinks(o.links)
  if (links.length > MAX_LINKS) return `Add at most ${MAX_LINKS} links.`
  if (links.some(l => !/^https?:\/\/\S+$/i.test(l))) return 'Each link must start with http:// or https://.'
  if (o.compare && o.compareWith.trim().length < 2) return 'Name the second topic to compare, or turn comparison off.'
  if (!/^[A-Za-z][A-Za-z .()'-]{1,39}$/.test(o.language.trim() || 'English')) return 'Language should be a name such as English, Spanish or Hindi.'
  return ''
}

export function composeTopic(topic: string, o: Options): string {
  const main = topic.trim()
  return o.compare && o.compareWith.trim() ? `Compare ${main} versus ${o.compareWith.trim()}` : main
}

export function toRequestOptions(o: Options) {
  return {
    style: o.compare ? 'comparison' : o.style,
    audience: o.audience,
    length: o.length,
    language: o.language.trim() || 'English',
    time_range: o.timeRange || null,
    urls: parseLinks(o.links),
    notes: o.notes.trim(),
    verify_claims: o.verify,
  }
}

/** True when any choice differs from the defaults (used to hint that options are active). */
export function hasCustomOptions(o: Options): boolean {
  return JSON.stringify({ ...o, verify: true }) !== JSON.stringify({ ...DEFAULT_OPTIONS })
}
