import type React from 'react'
import { useEffect, useRef, useState } from 'react'
import { problemText, useLang, useT } from '../i18n'
import { api, ApiError, type PublicConfig } from '../lib/api'
import { useSession } from '../lib/session'

// Seeded by `python -m app.seed` (backend/app/seed.py); shown only with DEV_LOGIN
const DEMO_USERS: { group: 'buyers' | 'sellers' | 'platform'; email: string; label: string }[] = [
  { group: 'buyers', email: 'arax@easo.local', label: 'Cafe Arax — owner' },
  { group: 'buyers', email: 'chef@easo.local', label: 'Cafe Arax — chef' },
  { group: 'buyers', email: 'nova@easo.local', label: 'Bar Nova — owner' },
  { group: 'sellers', email: 'lori@easo.local', label: 'Լոռի Պանիր — seller' },
  { group: 'sellers', email: 'pack@easo.local', label: 'Yerevan Pack — seller' },
  { group: 'sellers', email: 'farm@easo.local', label: 'Green Valley — seller' },
  { group: 'sellers', email: 'manager@easo.local', label: 'Լոռի Պանիր — manager' },
  { group: 'sellers', email: 'baker@easo.local', label: 'Ararat Bakery — buys and sells' },
  { group: 'sellers', email: 'honey@easo.local', label: 'Dilijan Honey — not verified yet' },
  { group: 'platform', email: 'newbie@easo.local', label: 'No company yet' },
  { group: 'platform', email: 'admin@easo.local', label: 'Easo operator' },
]

declare global {
  interface Window {
    google?: { accounts: { id: { initialize: (o: object) => void; renderButton: (el: HTMLElement, o: object) => void } } }
    onTelegramAuth?: (user: Record<string, unknown>) => void
  }
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve()
    const s = document.createElement('script')
    s.src = src
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error(src))
    document.head.appendChild(s)
  })
}

export function Login() {
  const t = useT()
  const lang = useLang()
  const { signIn } = useSession()
  const [config, setConfig] = useState<PublicConfig | null>(null)
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [sent, setSent] = useState(false)
  const [devCode, setDevCode] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const googleBox = useRef<HTMLDivElement>(null)
  const telegramBox = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.config().then(setConfig, (e: Error) => setError(e.message))
  }, [])

  async function done(accessToken: string) {
    // Signed in, the app swaps its routes and /login leads on (AfterSignIn in App)
    await signIn(accessToken)
  }

  function fail(e: unknown) {
    const ex = e as ApiError
    setError(problemText(ex.problems?.[0]?.code ?? ex.code, lang))
  }

  // Google Identity Services button
  useEffect(() => {
    const clientId = config?.google_client_id
    if (!clientId || !googleBox.current) return
    loadScript('https://accounts.google.com/gsi/client').then(() => {
      window.google?.accounts.id.initialize({
        client_id: clientId,
        callback: (r: { credential: string }) => api.google(r.credential).then((x) => done(x.access_token), fail),
      })
      if (googleBox.current) window.google?.accounts.id.renderButton(googleBox.current, { shape: 'pill', size: 'large', width: 320 })
    }, fail)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.google_client_id])

  // Telegram Login Widget (the site domain must be set in @BotFather with /setdomain)
  useEffect(() => {
    const bot = config?.telegram_bot_username
    const box = telegramBox.current
    if (!bot || !box || box.childElementCount) return
    window.onTelegramAuth = (user) => void api.telegram(user).then((x) => done(x.access_token), fail)
    const s = document.createElement('script')
    s.src = 'https://telegram.org/js/telegram-widget.js?22'
    s.async = true
    s.setAttribute('data-telegram-login', bot)
    s.setAttribute('data-size', 'large')
    s.setAttribute('data-radius', '20')
    s.setAttribute('data-onauth', 'onTelegramAuth(user)')
    s.setAttribute('data-request-access', 'write')
    box.appendChild(s)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.telegram_bot_username])

  async function start(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.emailStart(email, lang)
      setSent(true)
      setDevCode(r.dev_code)
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.emailVerify(email, code)
      await done(r.access_token)
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login page">
      <h1 className="tagline">{t('tagline')}</h1>
      <section className="ticket" style={{ display: 'grid', gap: 12 }}>
        <h2 style={{ margin: 0 }}>{t('login_email')}</h2>
        {!sent ? (
          <form onSubmit={start} style={{ display: 'grid', gap: 10 }}>
            <label className="field">
              <span>{t('email')}</span>
              <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <button className="btn primary block" disabled={busy}>
              {t('send_code')} →
            </button>
          </form>
        ) : (
          <form onSubmit={verify} style={{ display: 'grid', gap: 10 }}>
            <p className="muted small" style={{ margin: 0 }}>
              {t('code_sent')} <b>{email}</b>
            </p>
            {devCode && (
              <p className="notice" style={{ margin: 0 }}>
                {t('dev_code')}: <b className="num">{devCode}</b>
              </p>
            )}
            <label className="field">
              <span>{t('code')}</span>
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              />
            </label>
            <button className="btn primary block" disabled={busy || code.length < 6}>
              {t('sign_in')} →
            </button>
            <button type="button" className="link" onClick={() => (setSent(false), setCode(''), setDevCode(null))}>
              {t('change_email')}
            </button>
          </form>
        )}
        {error && <p className="error">{error}</p>}
        {(config?.google_client_id || config?.telegram_bot_username) && (
          <>
            <p className="section-title" style={{ textAlign: 'center' }}>
              {t('or_continue')}
            </p>
            <div style={{ display: 'grid', justifyItems: 'center', gap: 10 }}>
              {config.google_client_id && <div ref={googleBox} />}
              {config.telegram_bot_username && <div ref={telegramBox} />}
            </div>
          </>
        )}
      </section>

      {config?.dev_login && (
        <section className="ticket" style={{ marginTop: 12 }}>
          <h2 style={{ margin: 0 }}>{t('demo_users')}</h2>
          <p className="muted small">{t('login_dev_note')}</p>
          {(['buyers', 'sellers', 'platform'] as const).map((g) => (
            <div key={g}>
              <h3 className="section-title">{t(g)}</h3>
              <ul className="demo-users">
                {DEMO_USERS.filter((u) => u.group === g).map((u) => (
                  <li key={u.email}>
                    <button onClick={() => api.devLogin(u.email).then((r) => done(r.access_token), fail)}>
                      <span>{u.label}</span>
                      <small>{u.email}</small>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
