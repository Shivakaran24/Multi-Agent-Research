import { hasCustomOptions, optionsProblem, type Audience, type Length, type Options, type Style, type TimeRange } from './options'

const STYLES: [Style, string][] = [['brief', 'Detailed brief'], ['executive', 'Executive summary'], ['pros_cons', 'Pros and cons'], ['comparison', 'Comparison table']]
const AUDIENCES: [Audience, string][] = [['general', 'General reader'], ['executive', 'Executive'], ['technical', 'Technical'], ['student', 'Student']]
const LENGTHS: [Length, string][] = [['short', 'Short'], ['standard', 'Standard'], ['long', 'Long']]
const WINDOWS: [TimeRange, string][] = [['', 'Any time'], ['year', 'Last 12 months'], ['month', 'Last month']]
const LANGUAGES = ['English', 'Spanish', 'French', 'German', 'Portuguese', 'Hindi', 'Arabic', 'Japanese', 'Chinese']

/** Everything that shapes a brief, tucked under one disclosure so the composer stays simple. */
export default function ResearchOptionsPanel({ value, onChange }: { value: Options; onChange: (next: Options) => void }) {
  const set = <K extends keyof Options>(key: K, next: Options[K]) => onChange({ ...value, [key]: next })
  const problem = optionsProblem(value)
  return <details className="options-panel">
    <summary>Options{hasCustomOptions(value) && <span className="options-dot" aria-label="Custom options are on" />}</summary>
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
    <label className="options-block">Background notes <small>context to focus the research (not cited as evidence)</small>
      <textarea rows={2} maxLength={6000} placeholder="e.g. We are a utility in Spain; focus on Europe." value={value.notes} onChange={e => set('notes', e.target.value)} />
    </label>
    {problem && <div className="error-box" role="alert"><span>!</span> {problem}</div>}
  </details>
}
