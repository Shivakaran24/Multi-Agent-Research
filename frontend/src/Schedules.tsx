import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { hasCustomOptions, toStoredOptions, type Options } from './options'
import { createSchedule, deleteSchedule, listSchedules, runScheduleNow, setScheduleEnabled, type Frequency, type Schedule } from './runs'

const LABEL: Record<Frequency, string> = { daily: 'Every day', weekly: 'Every week', monthly: 'Every month' }

function when(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Recurring research: re-run a topic on a schedule and get each new brief by email. */
export default function Schedules({ token, onClose, onOpenRun, onChanged, options }: { token: string; onClose: () => void; onOpenRun: (id: string) => void; onChanged: () => void; options?: Options }) {
  const [items, setItems] = useState<Schedule[]>([])
  const [limit, setLimit] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [topic, setTopic] = useState('')
  const [frequency, setFrequency] = useState<Frequency>('weekly')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [useOptions, setUseOptions] = useState(true)
  const custom = !!options && hasCustomOptions(options)

  const load = useCallback(async () => {
    try { const data = await listSchedules(token); setItems(data.schedules); setLimit(data.limit); setError('') } catch (e) { setError(e instanceof Error ? e.message : 'Could not load schedules.') } finally { setLoaded(true) }
  }, [token])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function act(work: () => Promise<unknown>, success = '') {
    setBusy(true); setError(''); setNote('')
    try { await work(); if (success) setNote(success); await load(); onChanged() } catch (e) { setError(e instanceof Error ? e.message : 'Something went wrong.') } finally { setBusy(false) }
  }

  const add = (event: FormEvent) => {
    event.preventDefault()
    if (topic.trim().length < 3) return setError('Describe what to research (at least 3 characters).')
    void act(async () => { await createSchedule(token, topic.trim(), frequency, custom && useOptions && options ? toStoredOptions(options) : undefined); setTopic('') }, 'Scheduled. The first run happens after one full period; use “Run now” to try it immediately.')
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <section className="settings-modal schedules-modal" role="dialog" aria-modal="true" aria-labelledby="schedules-title">
      <button className="modal-close" aria-label="Close" onClick={onClose}>×</button>
      <div className="modal-icon">↻</div>
      <h2 id="schedules-title">Recurring research</h2>
      <p>Re-run a topic automatically and get each new brief by email, with a short note on what changed. Runs use your normal daily limits.</p>

      {!loaded && <div className="auth-loading" role="status"><span className="spinner" /></div>}
      {loaded && items.length === 0 && <div className="empty-history">No recurring research yet.</div>}
      <ul className="schedule-list">
        {items.map(item => <li key={item.id} className={item.enabled ? '' : 'paused'}>
          <div className="schedule-main">
            <strong>{item.topic}</strong>
            <small>{LABEL[item.frequency]} · {item.enabled ? `next ${when(item.next_run_at)}` : 'paused'}{item.last_status ? ` · last: ${item.last_status}` : ''}</small>
          </div>
          <div className="schedule-actions">
            {item.last_thread_id && <button type="button" className="text-button" onClick={() => { onOpenRun(item.last_thread_id!); onClose() }}>Open last</button>}
            <button type="button" className="text-button" disabled={busy} onClick={() => void act(() => runScheduleNow(token, item.id), 'Started. The new brief will appear in your history shortly.')}>Run now</button>
            <button type="button" className="text-button" disabled={busy} onClick={() => void act(() => setScheduleEnabled(token, item.id, !item.enabled))}>{item.enabled ? 'Pause' : 'Resume'}</button>
            <button type="button" className="text-button danger" disabled={busy} onClick={() => { if (window.confirm('Delete this recurring research? Past briefs stay in your history.')) void act(() => deleteSchedule(token, item.id)) }}>Delete</button>
          </div>
        </li>)}
      </ul>

      {loaded && items.length < limit && <form className="schedule-form" onSubmit={add}>
        <label htmlFor="schedule-topic">New recurring research</label>
        <input id="schedule-topic" maxLength={2000} placeholder="e.g. Latest developments in grid-scale batteries" value={topic} onChange={e => setTopic(e.target.value)} />
        {custom && <label className="options-check"><input type="checkbox" checked={useOptions} onChange={e => setUseOptions(e.target.checked)} /> Use the options currently set in the composer <small>style, audience, length, language, time window</small></label>}
        <div className="schedule-form-row">
          <select aria-label="How often" value={frequency} onChange={e => setFrequency(e.target.value as Frequency)}>
            {(Object.keys(LABEL) as Frequency[]).map(f => <option key={f} value={f}>{LABEL[f]}</option>)}
          </select>
          <button className="start-button" disabled={busy || topic.trim().length < 3}>Add schedule</button>
        </div>
      </form>}
      {loaded && items.length >= limit && <div className="auth-notice">You can keep up to {limit} recurring researches. Delete one to add another.</div>}
      {error && <div className="error-box" role="alert"><span>!</span> {error}</div>}
      {note && <div className="auth-notice" role="status">{note}</div>}
    </section>
  </div>
}
