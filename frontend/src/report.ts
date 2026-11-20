import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { hostOf, isWebUrl, linkCitations, splitReport, type SourceRef } from './citations'

export { hostOf, linkCitations, splitReport }
export type { SourceRef }

// Links inside a report always open in a new tab and never leak the opener.
DOMPurify.addHook('afterSanitizeAttributes', node => {
  if (node.tagName === 'A' && node.getAttribute('href')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export type ReportView = { html: string; sources: SourceRef[] }

export function buildReportView(markdown: string): ReportView {
  const { body, sources } = splitReport(markdown)
  const html = DOMPurify.sanitize(marked.parse(linkCitations(body, sources)) as string, { ADD_ATTR: ['target'] })
    .replace(/<em>\(unverified\)<\/em>/g, '<em class="unverified">⚠ unverified</em>')
  return { html, sources }
}

const escapeHtml = (text: string) => text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

/** A self-contained document that opens cleanly in a browser or Word. */
export function standaloneHtml(title: string, view: ReportView): string {
  const sources = view.sources.length
    ? `<h2>Sources</h2><ol>${view.sources.map(s => `<li value="${s.n}">${isWebUrl(s.url) ? `<a href="${escapeHtml(s.url)}">${escapeHtml(s.title)}</a>` : escapeHtml(s.title)} <small>${escapeHtml(hostOf(s.url))}</small></li>`).join('')}</ol>`
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
