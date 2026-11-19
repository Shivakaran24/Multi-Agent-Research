import { marked } from 'marked'
import DOMPurify from 'dompurify'

export type SourceRef = { n: number; title: string; url: string }

// Links inside a report always open in a new tab and never leak the opener.
DOMPurify.addHook('afterSanitizeAttributes', node => {
  if (node.tagName === 'A' && node.getAttribute('href')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

const SOURCE_LINE = /^\[(\d+)\]\s+(.*)\s+—\s+(https?:\/\/\S+)\s*$/

/** The writer appends "## Sources" with lines like `[1] Title — https://…`; split that off. */
export function splitReport(markdown: string): { body: string; sources: SourceRef[] } {
  const match = /\n##\s+Sources\s*\n/i.exec(markdown)
  if (!match) return { body: markdown, sources: [] }
  const sources: SourceRef[] = []
  for (const line of markdown.slice(match.index + match[0].length).split('\n')) {
    const found = SOURCE_LINE.exec(line.trim())
    if (found) sources.push({ n: Number(found[1]), title: found[2].trim(), url: found[3] })
  }
  return { body: markdown.slice(0, match.index), sources }
}

/** Turn inline citations like [2] into links to that source (only when the source is known). */
export function linkCitations(body: string, sources: SourceRef[]): string {
  const byNumber = new Map(sources.map(source => [source.n, source.url]))
  return body.replace(/\[(\d+)\](?!\()/g, (whole, digits: string) => {
    const url = byNumber.get(Number(digits))
    return url ? `[[${digits}]](${url.replace(/\(/g, '%28').replace(/\)/g, '%29')})` : whole
  })
}

export function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' }
}

export type ReportView = { html: string; sources: SourceRef[] }

export function buildReportView(markdown: string): ReportView {
  const { body, sources } = splitReport(markdown)
  const html = DOMPurify.sanitize(marked.parse(linkCitations(body, sources)) as string, { ADD_ATTR: ['target'] })
  return { html, sources }
}

const escapeHtml = (text: string) => text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

/** A self-contained document that opens cleanly in a browser or Word. */
export function standaloneHtml(title: string, view: ReportView): string {
  const sources = view.sources.length
    ? `<h2>Sources</h2><ol>${view.sources.map(s => `<li value="${s.n}"><a href="${escapeHtml(s.url)}">${escapeHtml(s.title)}</a> <small>${escapeHtml(hostOf(s.url))}</small></li>`).join('')}</ol>`
    : ''
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>body{font:16px/1.65 Georgia,serif;max-width:760px;margin:40px auto;padding:0 20px;color:#222}h1,h2,h3{line-height:1.25}a{color:#2b694a}small{color:#777}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 8px}</style></head><body>${view.html}${sources}</body></html>`
}

export function reportTitle(markdown: string, fallback: string): string {
  const first = markdown.trimStart().split('\n', 1)[0]
  return first.startsWith('# ') ? first.slice(2).trim() || fallback : fallback
}

export function fileSafe(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'research-brief'
}

export function downloadText(filename: string, text: string, mime: string) {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function downloadBlob(filename: string, bytes: Uint8Array, mime: string) {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: mime }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
