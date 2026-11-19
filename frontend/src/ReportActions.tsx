import { useEffect, useRef, useState } from 'react'
import { createSchedule, createShare, emailBrief, getRun, revokeShare, setFeedback, setTags, shareUrl, type Frequency } from './runs'

/** Thumbs up/down and a private-by-default, revocable read-only link for a finished brief. */
export default function ReportActions({ token, thread, onChanged }: { token: string; thread: string; onChanged?: () => void }) {
  const [feedback, setFeedbackState] = useState<number>(0)
  const [share, setShare] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const touched = useRef(false)
  const [topic, setTopic] = useState('')
  const [tags, setTagList] = useState<string[]>([])
  const [tagText, setTagText] = useState('')
  const [mailing, setMailing] = useState(false)
  const [mailTo, setMailTo] = useState('')

  useEffect(() => {
    let live = true
    touched.current = false
    setFeedbackState(0); setShare(''); setNote(''); setTagList([]); setMailing(false)
    getRun(token, thread).then(run => { if (live && !touched.current) { setFeedbackState(run.feedback ?? 0); setShare(run.share_token ?? ''); setTopic(run.topic); setTagList(run.tags ?? []) } }).catch(() => undefined)
    return () => { live = false }
  }, [token, thread])

  async function rate(value: 1 | -1) {
    touched.current = true
    const next = feedback === value ? 0 : value
    setFeedbackState(next)
    try { await setFeedback(token, thread, next) } catch (e) { setFeedbackState(feedback); setNote(e instanceof Error ? e.message : 'Could not save feedback.') }
  }

  async function enable() {
    touched.current = true
    setBusy(true); setNote('')
    try { setShare(await createShare(token, thread)) } catch (e) { setNote(e instanceof Error ? e.message : 'Could not create a link.') } finally { setBusy(false) }
  }

  async function disable() {
    setBusy(true); setNote('')
    try { await revokeShare(token, thread); setShare('') } catch (e) { setNote(e instanceof Error ? e.message : 'Could not turn the link off.') } finally { setBusy(false) }
  }

  async function repeat(frequency: Frequency) {
    setNote('')
    try { await createSchedule(token, topic, frequency); setNote(`Scheduled ${frequency}. Manage it under Recurring research.`) } catch (e) { setNote(e instanceof Error ? e.message : 'Could not schedule this.') }
  }

  async function saveTags(next: string[]) {
    try { setTagList(await setTags(token, thread, next)); onChanged?.() } catch (e) { setNote(e instanceof Error ? e.message : 'Could not save tags.') }
  }

  async function addTag(event: React.FormEvent) {
    event.preventDefault()
    const tag = tagText.trim().toLowerCase()
    setTagText('')
    if (tag && !tags.includes(tag)) await saveTags([...tags, tag])
  }

  async function sendEmail(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true); setNote('')
    try { setNote(await emailBrief(token, thread, mailTo.trim() || undefined)); setMailing(false); setMailTo('') } catch (e) { setNote(e instanceof Error ? e.message : 'Could not send the email.') } finally { setBusy(false) }
  }

  async function copy() {
    try { await navigator.clipboard.writeText(shareUrl(share)); setNote('Link copied.') } catch { setNote('Copy the link from the box.') }
  }

  return <section className="report-actions-bar" aria-label="Rate and share">
    <div className="rate">
      <span>Was this brief useful?</span>
      <button type="button" aria-label="Helpful" aria-pressed={feedback === 1} className={feedback === 1 ? 'on' : ''} onClick={() => void rate(1)}>👍</button>
      <button type="button" aria-label="Not helpful" aria-pressed={feedback === -1} className={feedback === -1 ? 'on' : ''} onClick={() => void rate(-1)}>👎</button>
    </div>
    <div className="share">
      {!share
        ? <button type="button" className="copy-button" disabled={busy} onClick={() => void enable()}>Create share link</button>
        : <div className="share-box">
            <input readOnly aria-label="Share link" value={shareUrl(share)} onFocus={e => e.currentTarget.select()} />
            <button type="button" className="copy-button" onClick={() => void copy()}>Copy</button>
            <button type="button" className="text-button" disabled={busy} onClick={() => void disable()}>Stop sharing</button>
          </div>}
    </div>
    <div className="mail">
      {!mailing ? <button type="button" className="copy-button" onClick={() => setMailing(true)}>Email this brief</button>
        : <form className="share-box" onSubmit={e => void sendEmail(e)}>
            <input type="email" aria-label="Send the brief to" placeholder="Your email, or someone else's" value={mailTo} onChange={e => setMailTo(e.target.value)} />
            <button className="copy-button" disabled={busy}>Send</button>
            <button type="button" className="text-button" onClick={() => setMailing(false)}>Cancel</button>
          </form>}
    </div>
    <div className="tags-edit" aria-label="Tags">
      {tags.map(t => <span key={t} className="tag-chip">{t}<button type="button" aria-label={`Remove tag ${t}`} onClick={() => void saveTags(tags.filter(x => x !== t))}>×</button></span>)}
      {tags.length < 5 && <form onSubmit={e => void addTag(e)}><input aria-label="Add a tag" maxLength={24} placeholder="+ tag" value={tagText} onChange={e => setTagText(e.target.value)} /></form>}
    </div>
    {topic && <div className="repeat">
      <select aria-label="Repeat this research" value="" onChange={e => { if (e.target.value) void repeat(e.target.value as Frequency) }}>
        <option value="">Repeat this research…</option>
        <option value="daily">Every day</option>
        <option value="weekly">Every week</option>
        <option value="monthly">Every month</option>
      </select>
    </div>}
    {note && <small className="share-note" role="status">{note}</small>}
    {share && <small className="share-note">Anyone with this link can read the brief. They cannot see your account or other research.</small>}
  </section>
}
