import { useEffect, useMemo } from 'react'
import { buildReportView, hostOf } from './report'
import { SAMPLE_REPORT, SAMPLE_TOPIC } from './sample'

/** Modal that shows a pre-written example brief, so visitors can judge the output before signing in. */
export default function SampleReport({ onClose }: { onClose: () => void }) {
  const view = useMemo(() => buildReportView(SAMPLE_REPORT), [])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <section className="settings-modal sample-modal" role="dialog" aria-modal="true" aria-labelledby="sample-title">
      <button className="modal-close" aria-label="Close" onClick={onClose}>×</button>
      <div className="sample-label">EXAMPLE BRIEF</div>
      <h2 id="sample-title" className="sample-topic">{SAMPLE_TOPIC}</h2>
      <p className="sample-note">Written by hand from public sources to show the output format. A real run searches fresh sources for your own topic.</p>
      <article className="markdown" dangerouslySetInnerHTML={{ __html: view.html }} />
      {view.sources.length > 0 && <section className="sources" aria-label="Sources"><h3>Sources</h3><ol>{view.sources.map(source => <li key={source.n} value={source.n}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a><small>{hostOf(source.url)}</small></li>)}</ol></section>}
    </section>
  </div>
}
