import { useEffect, useState, type FormEvent } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { askFollowUp } from './runs'

type Turn = { question: string; answer: string }

const SUGGESTIONS = ['What are the biggest open questions?', 'Summarize this for a non-expert.', 'What evidence is weakest here?']

/** Ask questions about a finished brief. Answers come only from the brief itself. */
export default function FollowUp({ token, thread }: { token: string; thread: string }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { setTurns([]); setQuestion(''); setError('') }, [thread])

  async function ask(text: string) {
    const q = text.trim()
    if (q.length < 3 || busy) return
    setBusy(true); setError('')
    try {
      const answer = await askFollowUp(token, thread, q)
      setTurns(items => [...items, { question: q, answer }])
      setQuestion('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not get an answer.')
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => { e.preventDefault(); void ask(question) }

  return <section className="followup" aria-label="Follow-up questions">
    <div className="followup-head"><strong>Ask a follow-up</strong><small>Answers use only this brief and its numbered sources.</small></div>
    {turns.map((turn, i) => <div className="followup-turn" key={i}>
      <p className="followup-q">{turn.question}</p>
      <div className="markdown followup-a" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(turn.answer) as string) }} />
    </div>)}
    {turns.length === 0 && <div className="followup-suggestions">{SUGGESTIONS.map(s => <button key={s} type="button" disabled={busy} onClick={() => void ask(s)}>{s}</button>)}</div>}
    {error && <div className="error-box"><span>!</span> {error}</div>}
    <form className="followup-form" onSubmit={submit}>
      <input aria-label="Follow-up question" maxLength={500} placeholder="e.g. Go deeper on the second section" value={question} onChange={e => setQuestion(e.target.value)} disabled={busy} />
      <button className="start-button" disabled={busy || question.trim().length < 3}>{busy ? 'Thinking…' : 'Ask'}</button>
    </form>
  </section>
}
