import { useEffect, useState } from 'react'
import { formatTokens, getAdminStats, type AdminStats } from './runs'

/** Owner-only overview: usage, quality signals and recent failures. */
export default function AdminPage({ token, onClose }: { token: string; onClose: () => void }) {
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    getAdminStats(token).then(setStats).catch(e => setError(e instanceof Error ? e.message : 'Could not load the admin page.'))
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [token, onClose])

  const tile = (label: string, value: string | number, hint = '') => <div className="admin-tile" key={label}><small>{label}</small><strong>{value}</strong>{hint && <em>{hint}</em>}</div>

  return <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <section className="settings-modal admin-modal" role="dialog" aria-modal="true" aria-labelledby="admin-title">
      <button className="modal-close" aria-label="Close" onClick={onClose}>×</button>
      <h2 id="admin-title">Admin overview</h2>
      {error && <div className="error-box" role="alert"><span>!</span> {error}</div>}
      {!stats && !error && <div className="auth-loading" role="status"><span className="spinner" /></div>}
      {stats && <>
        <div className="admin-tiles">
          {tile('Users', stats.users, `${stats.new_users_7d} new this week`)}
          {tile('Runs', stats.runs_total, `${stats.runs_24h} today · ${stats.runs_7d} this week`)}
          {tile('Tokens', formatTokens(stats.tokens_total), `${formatTokens(stats.tokens_24h)} in 24h`)}
          {tile('Ratings', `👍 ${stats.feedback_up}  👎 ${stats.feedback_down}`)}
          {tile('Schedules', stats.schedules_active, `${stats.schedules} total`)}
        </div>
        <h3>Runs by status</h3>
        <p className="admin-status">{Object.entries(stats.runs_by_status).map(([k, v]) => <span key={k}>{k}: <b>{v}</b></span>)}</p>
        <h3>Top users by tokens</h3>
        <table className="admin-table"><tbody>{stats.top_users.map(u => <tr key={u.email}><td>{u.email}</td><td>{u.runs} runs</td><td>{formatTokens(u.tokens)} tokens</td></tr>)}</tbody></table>
        <h3>Recent failures</h3>
        {stats.recent_failures.length === 0 ? <p className="empty-history">None. Nice.</p> : <table className="admin-table"><tbody>{stats.recent_failures.map((f, i) => <tr key={i}><td>{f.topic}</td><td>{f.status}</td><td>{f.user}</td><td>{new Date(f.created_at).toLocaleString()}</td></tr>)}</tbody></table>}
      </>}
    </section>
  </div>
}
