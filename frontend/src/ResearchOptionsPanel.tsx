import { useEffect, useRef, useState } from 'react'
import { fromStoredOptions, hasCustomOptions, MAX_DOCUMENTS, MAX_PDF_BYTES, optionsProblem, toStoredOptions, type Audience, type Length, type Options, type Style, type TimeRange } from './options'
import { deleteTemplate, listTemplates, saveTemplate, uploadPdf, type Template } from './runs'

const STYLES: [Style, string][] = [['brief', 'Detailed brief'], ['executive', 'Executive summary'], ['pros_cons', 'Pros and cons'], ['comparison', 'Comparison table']]
const AUDIENCES: [Audience, string][] = [['general', 'General reader'], ['executive', 'Executive'], ['technical', 'Technical'], ['student', 'Student']]
const LENGTHS: [Length, string][] = [['short', 'Short'], ['standard', 'Standard'], ['long', 'Long']]
const WINDOWS: [TimeRange, string][] = [['', 'Any time'], ['year', 'Last 12 months'], ['month', 'Last month']]
const LANGUAGES = ['English', 'Spanish', 'French', 'German', 'Portuguese', 'Hindi', 'Arabic', 'Japanese', 'Chinese']

/** Everything that shapes a brief, tucked under one disclosure so the composer stays simple. */
export default function ResearchOptionsPanel({ value, onChange, token, topic, onTopic }: { value: Options; onChange: (next: Options) => void; token: string; topic: string; onTopic: (next: string) => void }) {
  const set = <K extends keyof Options>(key: K, next: Options[K]) => onChange({ ...value, [key]: next })
  const problem = optionsProblem(value)
  const fileInput = useRef<HTMLInputElement>(null)
  const [templates, setTemplates] = useState<Template[]>([])
  const [picked, setPicked] = useState('')
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [reading, setReading] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => { listTemplates(token).then(setTemplates).catch(() => setTemplates([])) }, [token])

  async function addPdf(file: File | undefined) {
    if (!file) return
    setMessage('')
    if (value.documents.length >= MAX_DOCUMENTS) return setMessage(`Attach at most ${MAX_DOCUMENTS} PDFs.`)
    if (!/\.pdf$/i.test(file.name)) return setMessage('Choose a PDF file.')
    if (file.size > MAX_PDF_BYTES) return setMessage('That PDF is larger than 5 MB.')
    setReading(true)
    try {
      const pdf = await uploadPdf(token, file)
      onChange({ ...value, documents: [...value.documents.filter(d => d.name !== pdf.name), { name: pdf.name, text: pdf.text }] })
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not read that PDF.') } finally { setReading(false); if (fileInput.current) fileInput.current.value = '' }
  }

  function apply(id: string) {
    setPicked(id)
    const found = templates.find(t => t.id === id)
    if (!found) return
    onChange({ ...fromStoredOptions(found.options), documents: value.documents })
    if (found.topic) onTopic(found.topic)
  }

  async function save() {
    setMessage('')
    try {
      const made = await saveTemplate(token, name.trim(), topic.trim(), toStoredOptions(value))
      setTemplates(list => [...list, made]); setPicked(made.id); setSaving(false); setName(''); setMessage('Template saved.')
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not save the template.') }
  }

  async function remove() {
    const found = templates.find(t => t.id === picked)
    if (!found || !window.confirm(`Delete the template “${found.name}”?`)) return
    try { await deleteTemplate(token, found.id); setTemplates(list => list.filter(t => t.id !== found.id)); setPicked('') } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not delete the template.') }
  }
  return <details className="options-panel">
    <summary>Options{hasCustomOptions(value) && <span className="options-dot" aria-label="Custom options are on" />}</summary>
    <div className="template-row">
      <select aria-label="Saved templates" value={picked} onChange={e => apply(e.target.value)}>
        <option value="">{templates.length ? 'Apply a saved template…' : 'No saved templates yet'}</option>
        {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
      {picked && <button type="button" className="text-button danger" onClick={() => void remove()}>Delete</button>}
      {!saving && <button type="button" className="text-button" onClick={() => setSaving(true)}>Save current as template</button>}
      {saving && <><input aria-label="Template name" maxLength={60} placeholder="Template name" value={name} onChange={e => setName(e.target.value)} /><button type="button" className="text-button" disabled={!name.trim()} onClick={() => void save()}>Save</button><button type="button" className="text-button" onClick={() => setSaving(false)}>Cancel</button></>}
    </div>
    <div className="options-grid">
      <label>Style<select value={value.compare ? 'comparison' : value.style} disabled={value.compare} onChange={e => set('style', e.target.value as Style)}>{STYLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>Audience<select value={value.audience} onChange={e => set('audience', e.target.value as Audience)}>{AUDIENCES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>Length<select value={value.length} onChange={e => set('length', e.target.value as Length)}>{LENGTHS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>Sources from<select value={value.timeRange} onChange={e => set('timeRange', e.target.value as TimeRange)}>{WINDOWS.map(([v, l]) => <option key={v || 'any'} value={v}>{l}</option>)}</select></label>
      <label>Write in<input list="brief-languages" maxLength={40} value={value.language} onChange={e => set('language', e.target.value)} /><datalist id="brief-languages">{LANGUAGES.map(l => <option key={l} value={l} />)}</datalist></label>
      <label className="options-check"><input type="checkbox" checked={value.verify} onChange={e => set('verify', e.target.checked)} /> Check claims against sources <small>uses extra tokens</small></label>
    </div>
    <label className="options-check"><input type="checkbox" checked={value.compare} onChange={e => set('compare', e.target.checked)} /> Compare with a second topic</label>
    {value.compare && <input className="options-wide" aria-label="Second topic to compare" maxLength={900} placeholder="Second topic, e.g. pumped hydro storage" value={value.compareWith} onChange={e => set('compareWith', e.target.value)} />}
    <label className="options-block">Your own sources <small>public web page links, one per line (up to 5)</small>
      <textarea rows={2} placeholder="https://example.com/report" value={value.links} onChange={e => set('links', e.target.value)} />
    </label>
    <div className="options-block">Your own documents <small>PDFs with a text layer, up to {MAX_DOCUMENTS} files and 5 MB each; kept only for this run</small>
      <div className="pdf-row">
        <input ref={fileInput} type="file" accept="application/pdf,.pdf" aria-label="Upload a PDF" disabled={reading || value.documents.length >= MAX_DOCUMENTS} onChange={e => void addPdf(e.target.files?.[0])} />
        {reading && <span className="spinner" role="status" aria-label="Reading PDF" />}
      </div>
      {value.documents.length > 0 && <ul className="pdf-list">{value.documents.map(d => <li key={d.name}><span>{d.name}</span><button type="button" className="text-button danger" onClick={() => set('documents', value.documents.filter(x => x.name !== d.name))}>Remove</button></li>)}</ul>}
    </div>
    <label className="options-block">Background notes <small>context to focus the research (not cited as evidence)</small>
      <textarea rows={2} maxLength={6000} placeholder="e.g. We are a utility in Spain; focus on Europe." value={value.notes} onChange={e => set('notes', e.target.value)} />
    </label>
    {message && <div className="notice-box" role="status">{message}</div>}
    {problem && <div className="error-box" role="alert"><span>!</span> {problem}</div>}
  </details>
}
