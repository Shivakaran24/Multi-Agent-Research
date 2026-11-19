import { useMemo, useState } from 'react'

type Asset = {
  kind: 'image' | 'video' | 'model'
  title: string
  url: string
  description: string
  source_url: string
  thumbnail_url?: string
  embed_url?: string
}
type Visuals = { images: Asset[]; videos: Asset[]; models: Asset[]; warnings: string[] }
type Tab = keyof Pick<Visuals, 'images' | 'videos' | 'models'>

const tabs: { id: Tab; label: string; icon: string }[] = [
  { id: 'images', label: 'Images', icon: '▧' },
  { id: 'videos', label: 'Videos', icon: '▷' },
  { id: 'models', label: '3D models', icon: '◇' },
]

function safeHttps(value: string | undefined): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' ? url.toString() : undefined
  } catch {
    return undefined
  }
}

export default function VisualContext({ api, token, thread }: { api: string; token: string; thread: string }) {
  const [visuals, setVisuals] = useState<Visuals | null>(null)
  const [tab, setTab] = useState<Tab>('images')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<Asset | null>(null)
  const assets = useMemo(() => visuals?.[tab] || [], [visuals, tab])

  async function findVisuals() {
    setLoading(true)
    setError('')
    setPreview(null)
    try {
      const response = await fetch(`${api}/research/runs/${encodeURIComponent(thread)}/visuals`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ types: ['images', 'videos', 'models'] }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Visual search failed (${response.status}).`)
      const result = body as Visuals
      setVisuals(result)
      const firstTab = tabs.find(item => result[item.id]?.length)?.id || 'images'
      setTab(firstTab)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Visual search could not be completed.')
    } finally {
      setLoading(false)
    }
  }

  return <section className="visual-context">
    <div className="visual-heading">
      <span className="visual-icon">✳</span>
      <div className="visual-heading-copy"><strong>Visual context</strong><small>Images, videos, and interactive 3D references for this topic</small></div>
      {!visuals && <button className="visual-search-button" onClick={findVisuals} disabled={loading}>{loading ? <><span className="spinner" /> Searching</> : <>Find visuals <span>↗</span></>}</button>}
      {visuals && <button className="visual-refresh" onClick={findVisuals} disabled={loading}>{loading ? 'Searching…' : 'Refresh'}</button>}
    </div>
    {!visuals && <p className="visual-intro">Search trusted media pages for helpful visuals. Search uses additional Tavily requests. Visuals provide context; use the report’s cited sources to support factual claims.</p>}
    {error && <div className="visual-error">{error}</div>}
    {visuals && <>
      {visuals.warnings?.length > 0 && <div className="visual-warning">{visuals.warnings.join(' ')}</div>}
      <div className="visual-tabs" role="tablist" aria-label="Visual type">{tabs.map(item => <button key={item.id} className={tab === item.id ? 'active' : ''} role="tab" aria-selected={tab === item.id} onClick={() => { setTab(item.id); setPreview(null) }}><span>{item.icon}</span>{item.label}<small>{visuals[item.id]?.length || 0}</small></button>)}</div>
      {preview && <div className="visual-preview"><div className="visual-preview-head"><div><strong>{preview.title}</strong><small>{preview.kind === 'model' ? 'Interactive 3D model' : 'Video preview'}</small></div><button onClick={() => setPreview(null)} aria-label="Close preview">×</button></div>{safeHttps(preview.embed_url) && <iframe src={safeHttps(preview.embed_url)} title={preview.title} loading="lazy" referrerPolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-presentation" allow="fullscreen; picture-in-picture; accelerometer; autoplay; encrypted-media; gyroscope" allowFullScreen />}</div>}
      {assets.length === 0 ? <div className="visual-empty">No {tab === 'models' ? '3D models' : tab} found for this topic. Try refreshing or use the source links in the report.</div> : <div className="visual-grid">{assets.map((asset, index) => {
        const source = safeHttps(asset.source_url || asset.url)
        const image = tab === 'images' ? safeHttps(asset.url) : safeHttps(asset.thumbnail_url)
        return <article className="visual-card" key={`${asset.url}-${index}`}>
          {image ? <img src={image} alt={asset.description || asset.title} loading="lazy" referrerPolicy="no-referrer" onError={event => { event.currentTarget.style.display = 'none' }} /> : <div className={`visual-placeholder ${tab}`}>{tab === 'models' ? '3D' : '▶'}</div>}
          <div className="visual-card-copy"><strong title={asset.title}>{asset.title}</strong>{asset.description && <p>{asset.description}</p>}<div className="visual-card-actions">{asset.embed_url && <button onClick={() => setPreview(asset)}>Preview {tab === 'models' ? 'model' : 'video'}</button>}{source && <a href={source} target="_blank" rel="noopener noreferrer">Source ↗</a>}</div></div>
        </article>
      })}</div>}
      <p className="visual-rights">Media are hosted by their original publishers. Check source pages for context and reuse rights.</p>
    </>}
  </section>
}
