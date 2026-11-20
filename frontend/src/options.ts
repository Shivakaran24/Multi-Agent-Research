/** Per-run research choices: what the composer collects and what the API receives. Kept free of React so it can be unit-tested. */

export type Style = 'brief' | 'executive' | 'pros_cons' | 'comparison'
export type Audience = 'general' | 'executive' | 'technical' | 'student'
export type Length = 'short' | 'standard' | 'long'
export type TimeRange = '' | 'month' | 'year'

export type UserDocument = { name: string; text: string }

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
  documents: UserDocument[] // text read from uploaded PDFs
}

export const DEFAULT_OPTIONS: Options = {
  style: 'brief', audience: 'general', length: 'standard', language: 'English', timeRange: '',
  verify: true, links: '', notes: '', compare: false, compareWith: '', documents: [],
}

export const MAX_LINKS = 5
export const MAX_DOCUMENTS = 3
export const MAX_PDF_BYTES = 5 * 1024 * 1024

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
  if (o.documents.length > MAX_DOCUMENTS) return `Attach at most ${MAX_DOCUMENTS} PDFs.`
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
    documents: o.documents,
    verify_claims: o.verify,
  }
}

/** Options worth saving in a template or schedule: uploaded text is never stored. */
export function toStoredOptions(o: Options) {
  const { documents: _documents, ...rest } = toRequestOptions(o)
  return rest
}

type Stored = Partial<{ style: Style; audience: Audience; length: Length; language: string; time_range: TimeRange | null; urls: string[]; notes: string; verify_claims: boolean | null }>

/** The inverse of toStoredOptions, used when a template is applied to the composer. */
export function fromStoredOptions(stored: Stored | null | undefined): Options {
  const s = stored ?? {}
  return {
    ...DEFAULT_OPTIONS,
    style: s.style ?? DEFAULT_OPTIONS.style,
    audience: s.audience ?? DEFAULT_OPTIONS.audience,
    length: s.length ?? DEFAULT_OPTIONS.length,
    language: s.language || DEFAULT_OPTIONS.language,
    timeRange: s.time_range === 'year' || s.time_range === 'month' ? s.time_range : '',
    links: (s.urls ?? []).join('\n'),
    notes: s.notes ?? '',
    verify: s.verify_claims !== false,
  }
}

/** True when any choice differs from the defaults (used to hint that options are active). */
export function hasCustomOptions(o: Options): boolean {
  return JSON.stringify({ ...o, verify: true }) !== JSON.stringify({ ...DEFAULT_OPTIONS })
}
