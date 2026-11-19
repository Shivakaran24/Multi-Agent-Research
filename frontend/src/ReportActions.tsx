import { useEffect, useRef, useState } from 'react'
import { createShare, getRun, revokeShare, setFeedback, shareUrl } from './runs'

/** Thumbs up/down and a private-by-default, revocable read-only link for a finished brief. */
export default function ReportActions({ token, thread }: { token: string; thread: string }) {
  const [feedback, setFeedbackState] = useState<number>(0)
  const [share, setShare] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const touched = useRef(false)

  useEffect(() => {
    let live = true
    touched.current = false
    setFeedbackState(0); setShare(''); setNote('')
    getRun(token, thread).then(run => { if (live && !touched.current) { setFeedbackState(run.feedback ?? 0); setShare(run.share_token ?? '') } }).catch(() => undefined)
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
    {note && <small className="share-note" role="status">{note}</small>}
    {share && <small className="share-note">Anyone with this link can read the brief. They cannot see your account or other research.</small>}
  </section>
}
