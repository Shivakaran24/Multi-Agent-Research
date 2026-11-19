import { useState } from 'react'
import { resendVerification, useAuth } from './auth'

/** Shown to new accounts whose email is not confirmed yet (only when the server requires it). */
export default function VerifyBanner({ token, email }: { token: string; email: string }) {
  const { refreshUser } = useAuth()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  async function resend() {
    setBusy(true)
    try { setNote(await resendVerification(token)) } catch (e) { setNote(e instanceof Error ? e.message : 'Could not send the link.') } finally { setBusy(false) }
  }

  return <div className="verify-banner" role="status">
    <span>Confirm your email to start researching. We sent a link to <strong>{email}</strong>.</span>
    <span className="verify-actions">
      <button type="button" className="text-button" disabled={busy} onClick={() => void resend()}>Resend link</button>
      <button type="button" className="text-button" onClick={() => void refreshUser()}>I have confirmed</button>
    </span>
    {note && <small>{note}</small>}
  </div>
}
