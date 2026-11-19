import { useEffect, useRef, useState } from 'react'
import Logo from './Logo'
import { verifyEmail } from './auth'

/** Landing page for the link in the confirmation email. */
export default function VerifyEmail({ token }: { token: string }) {
  const [state, setState] = useState<{ kind: 'working' | 'ok' | 'error'; text: string }>({ kind: 'working', text: 'Confirming your email…' })
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    if (!token) { setState({ kind: 'error', text: 'This confirmation link is incomplete. Open the link from your email again.' }); return }
    verifyEmail(token)
      .then(text => setState({ kind: 'ok', text }))
      .catch(e => setState({ kind: 'error', text: e instanceof Error ? e.message : 'Could not confirm your email.' }))
  }, [token])

  return <div className="verify-shell"><div className="auth-card" role="status">
    <div className="brand"><Logo size={30} /><span>fieldwork<span className="brand-dot">.</span></span></div>
    <h1>{state.kind === 'ok' ? 'Email confirmed' : state.kind === 'error' ? 'Link problem' : 'One moment'}</h1>
    <p className={state.kind === 'error' ? 'error-box' : 'auth-notice'}>{state.text}</p>
    {state.kind !== 'working' && <button className="start-button auth-submit" onClick={() => { window.location.hash = '#/'; window.location.reload() }}>Continue to Fieldwork</button>}
  </div></div>
}
