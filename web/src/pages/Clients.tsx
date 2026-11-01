import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { problemText, useLang, useT } from '../i18n'
import { api, ApiError, type SellerClient, type SellerInvite } from '../lib/api'
import { date } from '../lib/format'

/** Seller cabinet: the standing link and QR to bring own clients (0% commission), and the list of clients. */
export function Clients() {
  const t = useT()
  const lang = useLang()
  const [invite, setInvite] = useState<SellerInvite | null>(null)
  const [clients, setClients] = useState<SellerClient[] | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fail = (e: ApiError) => setError(problemText(e.code, lang))
    api.sellerInvite().then(setInvite, fail)
    api.sellerClients().then(setClients, fail)
  }, [lang])

  async function copy() {
    if (!invite) return
    try {
      await navigator.clipboard.writeText(invite.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      window.prompt(t('inv_copy'), invite.url) // clipboard blocked: let them copy by hand
    }
  }

  async function share() {
    if (!invite) return
    try {
      await navigator.share({ title: 'Easo', text: t('inv_share_text'), url: invite.url })
    } catch {
      /* closed the share sheet */
    }
  }

  async function rotate() {
    if (!window.confirm(t('inv_rotate_confirm'))) return
    try {
      setInvite(await api.rotateInvite())
    } catch (e) {
      setError(problemText((e as ApiError).code, lang))
    }
  }

  const canShare = typeof navigator !== 'undefined' && 'share' in navigator

  return (
    <div className="stack page">
      <h1 className="page-title">{t('clients_title')}</h1>
      {error && <p className="error">{error}</p>}

      {invite && (
        <section className="ticket accent invite-card">
          <div className="invite-main">
            <p className="eyebrow">{t('inv_title')}</p>
            <p className="invite-url num">{invite.url.replace(/^https?:\/\//, '')}</p>
            <p className="invite-text">{t('inv_text')}</p>
            <div className="invite-actions">
              <button className="btn primary" onClick={copy}>
                {copied ? `✓ ${t('inv_copied')}` : t('inv_copy')}
              </button>
              {canShare && (
                <button className="btn ghost" onClick={share}>
                  {t('inv_share')}
                </button>
              )}
              <a className="btn ghost" href={api.supplierQr(invite.code)} download={`easo-${invite.code}.svg`}>
                {t('inv_qr')}
              </a>
              <Link className="btn ghost" to={`/s/${invite.code}`}>
                {t('inv_open')}
              </Link>
            </div>
          </div>
          <div className="invite-side">
            <img className="invite-qr" src={api.supplierQr(invite.code)} alt={`QR ${invite.url}`} width={168} height={168} />
            <p className="invite-count">
              <span className="big num">{invite.invited_clients}</span>
              <span>{t('inv_count')}</span>
            </p>
            <button className="link" onClick={rotate}>
              {t('inv_rotate')}
            </button>
          </div>
        </section>
      )}

      {clients && (
        <section className="ticket">
          {clients.length === 0 ? (
            <p className="muted">{t('clients_empty')}</p>
          ) : (
            <ul className="lines clients-list">
              {clients.map((c) => (
                <li key={c.buyer_id} className="line">
                  <span className="line-name">
                    <strong>{c.display_name}</strong>
                    <span className="muted small">
                      {t('clients_since')} {date(c.since, lang)}
                      {c.last_order_at && ` · ${t('clients_last')} ${date(c.last_order_at, lang)}`}
                    </span>
                  </span>
                  <span className="clients-meta">
                    <span className={c.source === 'seller_invited' ? 'pill brand' : 'pill plain'}>
                      {c.source === 'seller_invited' ? t('client_seller_invited') : t('client_platform')}
                    </span>
                    <span className="muted small">
                      {t('clients_orders')}: <span className="num">{c.orders}</span>
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
