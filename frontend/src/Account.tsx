import { useState, type FormEvent } from 'react'
import { API, useAuth, type User } from './auth'

const strong = (value: string) => value.length >= 8 && /[A-Za-z]/.test(value) && /\d/.test(value)

export default function Account({ user, onClose }: { user: User; onClose: () => void }) {
  const { logout, changePassword, deleteAccount } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [passwordError, setPasswordError] = useState('')
  const [passwordDone, setPasswordDone] = useState(false)
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const [busy, setBusy] = useState('')

  async function submitPassword(event: FormEvent) {
    event.preventDefault()
    setPasswordDone(false)
    if (!current) return setPasswordError('Enter your current password.')
    if (!strong(next)) return setPasswordError('New password needs at least 8 characters, with a letter and a number.')
    if (next !== confirm) return setPasswordError('New passwords do not match.')
    setBusy('password'); setPasswordError('')
    try {
      await changePassword(current, next)
      setCurrent(''); setNext(''); setConfirm(''); setPasswordDone(true)
    } catch (e) {
      setPasswordError(e instanceof Error ? e.message : 'Could not change the password.')
    } finally { setBusy('') }
  }

  async function submitDelete(event: FormEvent) {
    event.preventDefault()
    if (!deletePassword) return setDeleteError('Enter your password to confirm.')
    setBusy('delete'); setDeleteError('')
    try { await deleteAccount(deletePassword) } // signs out on success, which closes this dialog
    catch (e) { setDeleteError(e instanceof Error ? e.message : 'Could not delete the account.'); setBusy('') }
  }

  return <>
    <button className="modal-close" aria-label="Close" onClick={onClose}>×</button>
    <div className="modal-icon">◇</div>
    <h2 id="settings-title">Your account</h2>
    <p>You are signed in. Your research runs are private to this account.</p>
    <div className="account-details"><div><span>Name</span><strong>{user.full_name}</strong></div><div><span>Email</span><strong>{user.email}</strong></div></div>

    <details className="account-section" onToggle={() => { setPasswordDone(false); setPasswordError('') }}>
      <summary>Change password</summary>
      <form onSubmit={submitPassword} noValidate>
        <label htmlFor="acct-current">Current password</label>
        <input id="acct-current" type="password" autoComplete="current-password" maxLength={128} value={current} onChange={e => setCurrent(e.target.value)} />
        <label htmlFor="acct-new">New password</label>
        <input id="acct-new" type="password" autoComplete="new-password" maxLength={128} value={next} onChange={e => setNext(e.target.value)} />
        <label htmlFor="acct-confirm">Confirm new password</label>
        <input id="acct-confirm" type="password" autoComplete="new-password" maxLength={128} value={confirm} onChange={e => setConfirm(e.target.value)} />
        {passwordError && <div className="error-box" role="alert"><span>!</span> {passwordError}</div>}
        {passwordDone && <div className="auth-success" role="status">Password updated. Your other devices were signed out.</div>}
        <button className="start-button" type="submit" disabled={busy === 'password'}>{busy === 'password' ? 'Saving…' : 'Update password'}</button>
      </form>
    </details>

    <details className="account-section danger">
      <summary>Delete account</summary>
      <form onSubmit={submitDelete} noValidate>
        <p>This permanently deletes your account and every saved research brief. It cannot be undone.</p>
        <label htmlFor="acct-delete">Confirm with your password</label>
        <input id="acct-delete" type="password" autoComplete="current-password" maxLength={128} value={deletePassword} onChange={e => setDeletePassword(e.target.value)} />
        {deleteError && <div className="error-box" role="alert"><span>!</span> {deleteError}</div>}
        <button className="start-button danger-button" type="submit" disabled={busy === 'delete'}>{busy === 'delete' ? 'Deleting…' : 'Delete my account'}</button>
      </form>
    </details>

    <div className="api-endpoint"><span>API endpoint</span><code>{API}</code></div>
    <div className="modal-actions"><button className="text-button" onClick={() => logout()}>Sign out</button><button className="start-button" onClick={onClose}>Done <span>✓</span></button></div>
  </>
}
