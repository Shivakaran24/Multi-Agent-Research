import { useRef, useState, type MouseEvent } from 'react'
import { hostOf, type SourceRef } from './report'

type Card = { source: SourceRef; left: number; top: number }

/** The rendered brief. Hovering or focusing a citation like [3] shows which source it points to. */
export default function CitationArticle({ html, sources }: { html: string; sources: SourceRef[] }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [card, setCard] = useState<Card | null>(null)

  function show(event: MouseEvent<HTMLElement> | { target: EventTarget | null }) {
    const link = (event.target as HTMLElement | null)?.closest?.('a')
    const match = link && /^\[(\d+)\]$/.exec(link.textContent || '')
    const source = match ? sources.find(s => s.n === Number(match[1])) : undefined
    if (!link || !source || !wrap.current) return setCard(null)
    const box = link.getBoundingClientRect()
    const outer = wrap.current.getBoundingClientRect()
    setCard({ source, left: Math.min(Math.max(box.left - outer.left, 0), Math.max(outer.width - 280, 0)), top: box.bottom - outer.top + 6 })
  }

  return <div className="cite-wrap" ref={wrap} onMouseLeave={() => setCard(null)}>
    <article className="markdown" onMouseOver={show} onFocus={show} onBlur={() => setCard(null)} dangerouslySetInnerHTML={{ __html: html }} />
    {card && <div className="cite-card" role="tooltip" style={{ left: card.left, top: card.top }}>
      <strong>[{card.source.n}] {card.source.title}</strong>
      <small>{hostOf(card.source.url)}</small>
    </div>}
  </div>
}
