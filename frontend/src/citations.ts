export type SourceRef = { n: number; title: string; url: string }

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

