import { useEffect, useState, type FormEvent } from 'react'
import { API, fetchAuthConfig, forgotPassword, resetPassword, useAuth, type ServerAuthConfig } from './auth'
import Logo from './Logo'
import SampleReport from './SampleReport'

type Mode = 'login' | 'register' | 'forgot' | 'reset'
type Fields = { fullName: string; email: string; password: string; confirm: string; invite: string }
type FieldErrors = Partial<Record<keyof Fields, string>>

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function parseHash(): { mode: Mode; token: string } {
  const [path, query = ''] = window.location.hash.replace(/^#/, '').split('?')
  const mode: Mode = path === '/register' ? 'register' : path === '/forgot' ? 'forgot' : path === '/reset' ? 'reset' : 'login'
  return { mode, token: new URLSearchParams(query).get('token') || '' }
}

// Mirrors the server-side rules so most mistakes are caught before a request is sent.
function validate(mode: Mode, f: Fields): FieldErrors {
  const errors: FieldErrors = {}
  if (mode === 'register' && f.fullName.trim().length < 2) errors.fullName = 'Enter your full name.'
  if (mode !== 'reset' && !EMAIL.test(f.email.trim())) errors.email = 'Enter a valid email address.'
  if (mode === 'login') {
    if (!f.password) errors.password = 'Enter your password.'
  } else if (mode === 'register' || mode === 'reset') {
    if (f.password.length < 8) errors.password = 'Use at least 8 characters.'
    else if (!/[A-Za-z]/.test(f.password) || !/\d/.test(f.password)) errors.password = 'Include at least one letter and one number.'
    if (f.confirm !== f.password) errors.confirm = 'Passwords do not match.'
  }
  return errors
}

const TITLES: Record<Mode, [string, string]> = {
  login: ['Welcome back', 'Sign in to continue to your research desk.'],
  register: ['Create your account', 'Start researching in under a minute.'],
  forgot: ['Reset your password', 'Enter your email and we will send you a link to choose a new one.'],
  reset: ['Choose a new password', 'Use a password you have not used elsewhere.'],
}

export default function AuthPage() {
  const { login, register, notice } = useAuth()
  const initial = parseHash()
  const [mode, setMode] = useState<Mode>(initial.mode)
  const [resetToken, setResetToken] = useState(initial.token)
  const [config, setConfig] = useState<ServerAuthConfig | null>(null)
  const [fields, setFields] = useState<Fields>({ fullName: '', email: '', password: '', confirm: '', invite: '' })
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState('')
  const [flash, setFlash] = useState('')
  const [sent, setSent] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [showSample, setShowSample] = useState(false)
  const isRegister = mode === 'register'
  const registrationClosed = config?.registration_open === false
  const resetUnavailable = config?.password_reset_available === false

  useEffect(() => { fetchAuthConfig().then(setConfig) }, [])

  useEffect(() => {
    const sync = () => {
      const next = parseHash()
      if (next.token) setResetToken(next.token)
      setMode(next.mode); setErrors({}); setFormError(''); setSent(''); setShowPassword(false)
      setFields(current => ({ ...current, password: '', confirm: '' }))
    }
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  // Keep a reset token out of the address bar and browser history once it has been read.
  useEffect(() => {
    if (mode === 'reset' && window.location.hash.includes('token=')) {
      history.replaceState(null, '', window.location.pathname + window.location.search + '#/reset')
    }
  }, [mode])

  function update(key: keyof Fields, value: string) {
    setFields(current => ({ ...current, [key]: value }))
    if (errors[key]) setErrors(current => ({ ...current, [key]: undefined }))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const found = validate(mode, fields)
    setErrors(found)
    setFormError('')
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      if (mode === 'register') await register(fields.fullName.trim(), fields.email.trim(), fields.password, fields.invite.trim())
      else if (mode === 'login') await login(fields.email.trim(), fields.password)
      else if (mode === 'forgot') setSent(await forgotPassword(fields.email.trim()))
      else {
        setFlash(await resetPassword(resetToken, fields.password))
        window.location.hash = '#/login'
      }
      if (mode === 'register' || mode === 'login') history.replaceState(null, '', window.location.pathname + window.location.search)
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const fieldError = (key: keyof Fields) => errors[key] && <span className="auth-field-error" id={`${key}-error`} role="alert">{errors[key]}</span>
  const invalid = (key: keyof Fields) => ({ 'aria-invalid': errors[key] ? true : undefined, 'aria-describedby': errors[key] ? `${key}-error` : undefined })
  const [title, subtitle] = TITLES[mode]
  const passwordField = (label: string, id: 'password' | 'confirm', autoComplete: string, focus = false) => <div className="auth-field">
    <label htmlFor={id}>{label}</label>
    <div className="auth-password">
      <input id={id} type={showPassword ? 'text' : 'password'} autoComplete={autoComplete} autoFocus={focus} maxLength={128} value={fields[id]} onChange={e => update(id, e.target.value)} {...invalid(id)} />
      {id === 'password' && <button type="button" className="auth-toggle" onClick={() => setShowPassword(v => !v)} aria-pressed={showPassword}>{showPassword ? 'Hide' : 'Show'}</button>}
    </div>
    {fieldError(id) || (id === 'password' && (isRegister || mode === 'reset') && <span className="auth-hint">At least 8 characters, with a letter and a number.</span>)}
  </div>

  return <div className="auth-shell">
    <aside className="auth-aside">
      <div className="brand"><Logo size={30} /><span>fieldwork<span className="brand-dot">.</span></span></div>
      <div className="auth-aside-copy">
        <div className="eyebrow"><span className="eyebrow-line" /> RESEARCH, WITH RIGOR</div>
        <h2>Make sense of the complex.</h2>
        <p>A team of focused AI researchers finds the signal, checks the evidence, and brings it together in a cited brief.</p>
      </div>
      <ul className="auth-points">
        <li><span>✓</span> Human-reviewed research plans</li>
        <li><span>✓</span> Every claim traceable to a source</li>
        <li><span>✓</span> Your runs stay private to your account</li>
      </ul>
    </aside>

    <main className="auth-main">
      <form className="auth-card" onSubmit={submit} noValidate aria-labelledby="auth-title">
        <h1 id="auth-title">{title}</h1>
        <p className="auth-sub">{subtitle}</p>

        {flash && mode === 'login' && !formError && <div className="auth-success" role="status">{flash}</div>}
        {notice && mode === 'login' && !flash && !formError && <div className="auth-notice" role="status">{notice}</div>}
        {formError && <div className="error-box" role="alert"><span>!</span> {formError}</div>}

        {config?.unreachable && <div className="error-box" role="alert"><span>!</span> Cannot read the server's settings from {API}/auth/config. If you are the site owner, check that VITE_API_URL points at the API service (no trailing path) and that the API is running. Free hosts can take about a minute to wake up.</div>}
        {isRegister && registrationClosed && <div className="auth-notice" role="status">Registration is closed on this server. Ask an administrator to create an account for you.</div>}
        {mode === 'forgot' && resetUnavailable && <div className="auth-notice" role="status">Password reset by email is not set up on this server. Contact an administrator.</div>}
        {mode === 'reset' && !resetToken && <div className="error-box" role="alert"><span>!</span> This reset link is incomplete. Open the link from your email again, or <a href="#/forgot">request a new one</a>.</div>}

        {sent ? <div className="auth-success" role="status">{sent}</div> : <>
          {isRegister && <div className="auth-field">
            <label htmlFor="fullName">Full name</label>
            <input id="fullName" autoComplete="name" autoFocus maxLength={80} value={fields.fullName} onChange={e => update('fullName', e.target.value)} {...invalid('fullName')} />
            {fieldError('fullName')}
          </div>}

          {mode !== 'reset' && <div className="auth-field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" inputMode="email" autoComplete="email" autoFocus={!isRegister} maxLength={254} placeholder="you@example.com" value={fields.email} onChange={e => update('email', e.target.value)} {...invalid('email')} />
            {fieldError('email')}
          </div>}

          {isRegister && config?.invite_required && <div className="auth-field">
            <label htmlFor="invite">Invite code</label>
            <input id="invite" autoComplete="off" maxLength={100} placeholder="Ask the site owner for a code" value={fields.invite} onChange={e => update('invite', e.target.value)} />
          </div>}

          {mode !== 'forgot' && passwordField(mode === 'reset' ? 'New password' : 'Password', 'password', mode === 'login' ? 'current-password' : 'new-password', mode === 'reset')}
          {mode === 'login' && !resetUnavailable && <div className="auth-forgot"><a href="#/forgot">Forgot password?</a></div>}
          {(isRegister || mode === 'reset') && passwordField(mode === 'reset' ? 'Confirm new password' : 'Confirm password', 'confirm', 'new-password')}

          <button className="start-button auth-submit" type="submit" disabled={busy || (isRegister && registrationClosed) || (mode === 'forgot' && resetUnavailable) || (mode === 'reset' && !resetToken)}>
            {busy ? <span className="spinner auth-spinner" aria-hidden /> : null}
            {busy
              ? { login: 'Signing in…', register: 'Creating account…', forgot: 'Sending…', reset: 'Saving…' }[mode]
              : { login: 'Sign in', register: 'Create account', forgot: 'Send reset link', reset: 'Update password' }[mode]}
          </button>
        </>}

        <p className="auth-switch">
          {mode === 'login' && !registrationClosed && <>New to Fieldwork? <a href="#/register">Create an account</a></>}
          {mode === 'login' && registrationClosed && <>Need an account? Ask your administrator.</>}
          {isRegister && <>Already have an account? <a href="#/login">Sign in</a></>}
          {(mode === 'forgot' || mode === 'reset') && <>Remembered it? <a href="#/login">Back to sign in</a></>}
        </p>
        <button type="button" className="sample-link" onClick={() => setShowSample(true)}>See an example research brief <span>↗</span></button>
      </form>
    </main>
    {showSample && <SampleReport onClose={() => setShowSample(false)} />}
  </div>
}
