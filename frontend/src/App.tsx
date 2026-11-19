import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import VisualContext from './VisualContext'
import FollowUp from './FollowUp'
import Logo from './Logo'
import AuthPage from './AuthPage'
import Account from './Account'
import { API, errorMessage, useAuth, type Session } from './auth'
import { deleteRun, getRun, listRuns, QUESTION_MAX, SessionExpired, type Question, type RunStatus, type RunSummary, type Usage } from './runs'
import { buildReportView, downloadText, fileSafe, hostOf, reportTitle, standaloneHtml, type ReportView } from './report'

type RunEvent = { thread_id?: string; node?: string; update?: Record<string, unknown>; questions?: Question[]; report?: string; code?: string; message?: string }
type Phase = 'idle' | 'planning' | 'review' | 'research' | 'reviewing' | 'writing' | 'complete' | 'interrupted' | 'error'
const steps = [
  { id: 'lead_planner', label: 'Research plan', detail: 'Breaking your topic into focused questions', icon: '⌘' },
  { id: 'researcher', label: 'Parallel research', detail: 'Gathering sources and evidence', icon: '⌕' },
  { id: 'reviewer', label: 'Evidence review', detail: 'Checking coverage and identifying gaps', icon: '◉' },
  { id: 'writer', label: 'Final synthesis', detail: 'Writing a cited research brief', icon: '✳' },
]
const DEPTHS = [{ value: 1, label: 'Quick · 1 review' }, { value: 2, label: 'Standard · 2 reviews' }, { value: 3, label: 'Thorough · 3 reviews' }]
const TAGS: Partial<Record<RunStatus, string>> = { review: 'PLAN', failed: 'FAILED', interrupted: 'STOPPED', planning: 'LIVE', researching: 'LIVE' }
const LAST_RUN_KEY = 'fieldwork.lastRun'
const HELP_URL = 'https://github.com/Shivakaran24/Multi-Agent-Research#readme'

const readLastRun = () => { try { return sessionStorage.getItem(LAST_RUN_KEY) || '' } catch { return '' } }
const writeLastRun = (id: string) => { try { if (id) sessionStorage.setItem(LAST_RUN_KEY, id); else sessionStorage.removeItem(LAST_RUN_KEY) } catch { /* storage may be blocked */ } }

function ExportMenu({ markdown, view, topic }: { markdown: string; view: ReportView; topic: string }) {
  const title = reportTitle(markdown, topic || 'Research brief')
  const base = fileSafe(title)
  const done = (event: MouseEvent<HTMLButtonElement>) => (event.currentTarget.closest('details') as HTMLDetailsElement | null)?.removeAttribute('open')
  return <details className="export-menu">
    <summary className="copy-button">Export <span>▾</span></summary>
    <div className="export-list" role="menu">
      <button role="menuitem" onClick={e => { downloadText(`${base}.md`, markdown, 'text/markdown'); done(e) }}>Markdown <small>.md</small></button>
      <button role="menuitem" onClick={e => { downloadText(`${base}.html`, standaloneHtml(title, view), 'text/html'); done(e) }}>Web page <small>.html · opens in Word</small></button>
      <button role="menuitem" onClick={e => { done(e); window.print() }}>Print or save as PDF</button>
    </div>
  </details>
}

function Workspace({ session }: { session: Session }) {
  const { logout } = useAuth()
  const { token, user } = session
  const [topic, setTopic] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [thread, setThread] = useState('')
  const [questions, setQuestions] = useState<Question[]>([])
  const [report, setReport] = useState('')
  const [activeStep, setActiveStep] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [events, setEvents] = useState<string[]>([])
  const [iterations, setIterations] = useState(0)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [usage, setUsage] = useState<Usage | null>(null)
  const [depth, setDepth] = useState<number | null>(null)
  const [interruptedNote, setInterruptedNote] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const streamedRef = useRef(false) // true once the current request has produced any event
  const view = useMemo(() => buildReportView(report), [report])
  const busy = ['planning', 'research', 'reviewing', 'writing'].includes(phase)
  const effectiveDepth = depth ?? usage?.default_iterations ?? 1
  const depthOptions = DEPTHS.some(d => d.value === effectiveDepth) ? DEPTHS : [...DEPTHS, { value: effectiveDepth, label: `Custom · ${effectiveDepth} reviews` }]

  const expired = useCallback((e: unknown) => { if (e instanceof SessionExpired) { logout(e.message); return true } return false }, [logout])

  const refreshRuns = useCallback(async () => {
    try {
      const data = await listRuns(token)
      setRuns(data.runs)
      setUsage(data.usage)
    } catch (e) { expired(e) /* other failures just leave the previous list in place */ }
  }, [token, expired])

  async function consume(response: Response) {
    if (response.status === 401) {
      const message = 'Your session has expired. Please sign in again.'
      logout(message)
      throw new Error(message)
    }
    if (!response.ok) throw new Error(await errorMessage(response))
    if (!response.body) throw new Error('The server returned an empty stream.')
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
        const type = frame.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim()
        const dataLine = frame.split('\n').find(line => line.startsWith('data:'))
        if (type && dataLine) handleEvent(type, JSON.parse(dataLine.slice(5).trim()) as RunEvent)
        boundary = buffer.indexOf('\n\n')
      }
      if (done) break
    }
  }

  function handleEvent(type: string, data: RunEvent) {
    streamedRef.current = true
    if (data.thread_id) { setThread(data.thread_id); writeLastRun(data.thread_id) }
    if (type === 'started') { refreshRuns(); return }
    if (type === 'plan_review_required') { setQuestions(data.questions || []); setPhase('review'); return }
    if (type === 'completed') { setReport(data.report || ''); setPhase('complete'); setActiveStep('writer'); setEvents(items => ['Your research brief is ready.', ...items]); return }
    if (type === 'error') throw new Error(data.message || 'Research could not be completed.')
    if (type === 'progress' && data.node) {
      setActiveStep(data.node)
      const labels: Record<string, string> = { lead_planner: 'Research plan drafted', researcher: 'A researcher returned evidence', reviewer: 'Evidence review complete', writer: 'Report drafted' }
      if (data.node === 'researcher') setPhase('research')
      if (data.node === 'reviewer') { setPhase('reviewing'); setIterations(n => n + 1) }
      if (data.node === 'writer') setPhase('writing')
      if (labels[data.node]) setEvents(items => [labels[data.node!], ...items].slice(0, 8))
    }
  }

  /** `fallback` is where to land if the request fails before streaming anything (e.g. a limit or validation error). */
  async function run(request: (signal: AbortSignal) => Promise<Response>, fallback: Phase = 'error') {
    const controller = new AbortController()
    abortRef.current = controller
    streamedRef.current = false
    try {
      await consume(await request(controller.signal))
    } catch (e) { fail(e, streamedRef.current ? 'error' : fallback) } finally {
      if (abortRef.current === controller) abortRef.current = null
      refreshRuns()
    }
  }

  async function startResearch() {
    if (!topic.trim() || busy) return
    setError(''); setNotice(''); setReport(''); setEvents([]); setThread(''); setIterations(0); setActiveStep(''); setPhase('planning')
    await run(signal => fetch(`${API}/research/runs`, { method: 'POST', headers: headers(), signal, body: JSON.stringify({ topic: topic.trim(), max_iterations: effectiveDepth }) }))
  }
  async function approvePlan() {
    if (!thread) return
    setError(''); setNotice(''); setPhase('research')
    await run(signal => fetch(`${API}/research/runs/${encodeURIComponent(thread)}/resume`, { method: 'POST', headers: headers(), signal, body: JSON.stringify({ questions }) }), 'review')
  }
  function cancel() { abortRef.current?.abort() }
  function headers() { return { 'Content-Type': 'application/json', Authorization: `Bearer ${token.trim()}` } }
  function fail(e: unknown, landing: Phase = 'error') {
    if (e instanceof DOMException && e.name === 'AbortError') {
      setPhase('idle'); setNotice('Research cancelled. It stays in your history, and you can run it again any time.')
      return
    }
    setPhase(landing)
    setError(e instanceof TypeError ? 'Cannot reach the server. Check that the API is running and try again.' : e instanceof Error ? e.message : 'Something went wrong.')
  }
  function reset() {
    abortRef.current?.abort()
    writeLastRun('')
    setPhase('idle'); setTopic(''); setReport(''); setThread(''); setEvents([]); setQuestions([]); setError(''); setNotice(''); setActiveStep(''); setInterruptedNote('')
  }

  async function openRun(id: string, quiet = false) {
    if (busy) return
    try {
      const found = await getRun(token, id)
      setError(''); setNotice(''); setEvents([]); setIterations(0); setActiveStep(found.status === 'complete' ? 'writer' : '')
      setThread(found.thread_id); setTopic(found.topic); setQuestions(found.plan ?? []); setInterruptedNote(''); writeLastRun(found.thread_id)
      if (found.status === 'complete' && found.report) { setReport(found.report); setPhase('complete') }
      else if (found.status === 'review' && found.plan?.length) { setReport(''); setPhase('review') }
      else {
        setReport(''); setPhase('interrupted')
        setInterruptedNote(found.status === 'failed' ? 'This run hit an error before it finished.' : 'This run stopped before it finished.')
      }
    } catch (e) {
      if (expired(e)) return
      writeLastRun('')
      if (!quiet) setError(e instanceof Error ? e.message : 'Could not open that research.')
    }
  }

  async function removeRun(item: RunSummary) {
    if (busy || !window.confirm(`Delete "${item.title || item.topic}" from your history? This cannot be undone.`)) return
    try { await deleteRun(token, item.thread_id) } catch (e) { if (!expired(e)) setError(e instanceof Error ? e.message : 'Could not delete that research.'); return }
    if (item.thread_id === thread) reset()
    refreshRuns()
  }

  useEffect(() => {
    refreshRuns()
    const last = readLastRun()
    if (last) openRun(last, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); reset() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const remaining = usage?.daily_limit ? Math.max(usage.daily_limit - usage.used_today, 0) : null

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><Logo size={26} /><span>fieldwork<span className="brand-dot">.</span></span><span className="workspace-tag">WORKSPACE</span></div>
      <button className="new-research" onClick={reset}><span>＋</span> New research <kbd>{/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K'}</kbd></button>
      <div className="side-label">YOUR WORKSPACE</div>
      <button className="nav-item selected"><span>▦</span> Research desk</button>
      <div className="side-label recent-label">RECENT RESEARCH <span>{runs.length || ''}</span></div>
      {runs.length === 0 && <div className="empty-history">Your research will appear here.</div>}
      <nav className="history-list" aria-label="Research history">
        {runs.map(item => <div className={`history-row ${item.thread_id === thread ? 'active' : ''}`} key={item.thread_id}>
          <button className="history-item" disabled={busy} onClick={() => openRun(item.thread_id)} title={item.topic}>
            <span className={`recent-dot ${item.status}`} /><span className="history-text">{item.title || item.topic}</span>{TAGS[item.status] && <span className={`history-tag ${item.status}`}>{TAGS[item.status]}</span>}
          </button>
          <button className="history-delete" disabled={busy} aria-label={`Delete ${item.title || item.topic}`} onClick={() => removeRun(item)}>×</button>
        </div>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="security-note"><span className="shield">◇</span><div><strong>Private by design</strong><small>Only you can open your runs.</small></div></div>
        <button className="profile" onClick={() => setShowSettings(true)}><span className="avatar">{user.full_name.charAt(0).toUpperCase()}</span><span className="profile-name"><span className="profile-name-text">{user.full_name}</span><small>Account &amp; sign out</small></span><span className="more">•••</span></button>
      </div>
    </aside>

    <main className="main">
      <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> Research desk</div><div className="top-actions"><div className="secure-label"><i /> Secure connection</div><button className="icon-button" aria-label="Settings" onClick={() => setShowSettings(true)}>⚙</button><button className="help-button" onClick={() => window.open(HELP_URL, '_blank', 'noopener,noreferrer')}>Help <span>↗</span></button></div></header>
      <div className="content-wrap">
        <div className="eyebrow"><span className="eyebrow-line" /> RESEARCH, WITH RIGOR</div>
        <h1>{phase === 'complete' ? 'Your brief is ready.' : phase === 'review' ? 'Review your research plan.' : phase === 'interrupted' ? 'Pick up where you left off.' : busy ? 'The work is underway.' : 'Make sense of the complex.'}</h1>
        <p className="subtitle">{phase === 'complete' ? 'A source-backed synthesis, built from multiple research perspectives.' : phase === 'review' ? 'Your research team proposed these questions. Refine them or approve to begin.' : phase === 'interrupted' ? 'Nothing is lost: your topic is below, ready to run again.' : 'A team of focused AI researchers finds the signal, checks the evidence, and brings it together.'}</p>

        {phase === 'idle' || phase === 'error' ? <section className="composer">
          <label htmlFor="topic">What would you like to understand?</label>
          <textarea id="topic" maxLength={2000} placeholder="Ask a question, explore a market, or investigate a topic…" value={topic} onChange={e => setTopic(e.target.value)} onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') startResearch() }} />
          <div className="composer-footer"><span className="composer-options"><span className="sparkle">✳</span> Multi-agent research <span className="separator">·</span><label className="depth-picker">Depth <select aria-label="Research depth" value={effectiveDepth} onChange={e => setDepth(Number(e.target.value))}>{depthOptions.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}</select></label></span><button className="start-button" disabled={!topic.trim()} onClick={startResearch}>Start research <span>↗</span></button></div>
          {remaining !== null && <div className={`usage-line ${remaining === 0 ? 'out' : ''}`}>{remaining} of {usage!.daily_limit} runs left today · deeper runs use more of your search and model quota</div>}
          {notice && <div className="notice-box" role="status">{notice}</div>}
          {error && <div className="error-box"><span>!</span> {error}</div>}
        </section> : null}

        {phase === 'interrupted' && <section className="composer interrupted"><div className="interrupted-note"><span>!</span> {interruptedNote}</div><p className="interrupted-topic">{topic}</p><div className="composer-footer"><span>Running again starts a fresh plan.</span><button className="start-button" onClick={startResearch}>Run again <span>↗</span></button></div>{error && <div className="error-box"><span>!</span> {error}</div>}</section>}

        {phase === 'review' && <section className="plan-card"><div className="card-heading"><span className="card-icon">⌘</span><div><strong>Proposed research plan</strong><small>{questions.length} focused questions · Edit before the team starts</small></div><span className="review-pill">YOUR REVIEW</span></div>{questions.map((q, i) => <div className="question-row" key={i}><span className="question-num">0{i + 1}</span><div className="question-fields"><input aria-label={`Research question ${i + 1}`} maxLength={QUESTION_MAX} value={q.question} onChange={e => setQuestions(items => items.map((item, j) => j === i ? { ...item, question: e.target.value } : item))} /><textarea aria-label={`Rationale ${i + 1}`} maxLength={QUESTION_MAX} value={q.rationale} onChange={e => setQuestions(items => items.map((item, j) => j === i ? { ...item, rationale: e.target.value } : item))} /></div></div>)}{error && <div className="error-box plan-error"><span>!</span> {error}</div>}<div className="plan-footer"><button className="text-button" onClick={() => setQuestions(items => items.length < 8 ? [...items, { question: '', rationale: '' }] : items)}>＋ Add a question</button><button className="start-button" disabled={!questions.length || questions.some(q => q.question.trim().length < 8)} onClick={approvePlan}>Approve &amp; begin <span>↗</span></button></div></section>}

        {busy && <section className="progress-card"><div className="progress-title"><span className="pulse" /><div><strong>Research in progress</strong><small>{thread ? `Run ${thread.slice(0, 8)} · ` : ''}{iterations ? `Review round ${iterations}` : 'Your team is working through the evidence'}</small></div><span className="live-tag">LIVE</span><button className="cancel-button" onClick={cancel}>Cancel</button></div><div className="steps">{steps.map((step, i) => { const active = activeStep === step.id; const done = steps.findIndex(s => s.id === activeStep) > i || (phase === 'writing' && i < 3); return <div className={`step ${active ? 'active' : ''} ${done ? 'done' : ''}`} key={step.id}><span className="step-icon">{done ? '✓' : active && busy ? <span className="spinner" /> : step.icon}</span><span className="step-copy"><strong>{step.label}</strong><small>{step.detail}</small></span><span className="step-state">{done ? 'COMPLETE' : active ? 'IN PROGRESS' : 'UP NEXT'}</span></div> })}</div>{events.length > 0 && <div className="activity-line"><span className="activity-pip" />{events[0]}</div>}</section>}

        {phase === 'complete' && <section className="report-card"><div className="report-toolbar"><div className="report-label"><span className="report-check">✓</span><div><strong>Research brief</strong><small>Completed · {iterations || 1} evidence review{(iterations || 1) === 1 ? '' : 's'}</small></div></div><div className="report-actions"><ExportMenu markdown={report} view={view} topic={topic} /><button className="copy-button" onClick={() => navigator.clipboard.writeText(report).catch(() => setError('Clipboard access is unavailable.'))}>Copy brief <span>▣</span></button></div></div><article className="markdown" dangerouslySetInnerHTML={{ __html: view.html }} />{view.sources.length > 0 && <section className="sources" aria-label="Sources"><h3>Sources</h3><ol>{view.sources.map(source => <li key={source.n} value={source.n}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a><small>{hostOf(source.url)}</small></li>)}</ol></section>}<FollowUp token={token} thread={thread} /><VisualContext api={API} token={token} thread={thread} /></section>}

        <div className="trust-row"><span>BUILT FOR THOUGHTFUL WORK</span><i /><span>Human-reviewed plan</span><i /><span>Evidence checked</span><i /><span>Every claim traceable</span></div>
        {phase === 'idle' && <div className="example-prompts"><div className="example-heading">A few places to start <span>↘</span></div><div className="prompt-grid">{['How is AI changing clinical drug discovery?', 'What is driving the next wave of grid storage?', 'How are cities adapting to extreme heat?'].map((prompt, i) => <button key={prompt} onClick={() => setTopic(prompt)}><span className="prompt-index">0{i + 1}</span><span>{prompt}</span><span className="prompt-arrow">↗</span></button>)}</div></div>}
      </div>
      <footer className="footer"><span>FIELDWORK <span className="footer-dot">·</span> RESEARCH, MADE CLEAR</span><span>Thoughtful answers start with better questions.</span></footer>
    </main>

    {showSettings && <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) setShowSettings(false) }}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><Account user={user} onClose={() => setShowSettings(false)} /></section></div>}
  </div>
}

export default function App() {
  const { session, ready } = useAuth()
  if (!ready) return <div className="auth-loading" role="status" aria-label="Loading"><span className="spinner" /></div>
  return session ? <Workspace session={session} /> : <AuthPage />
}
