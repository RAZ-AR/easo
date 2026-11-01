import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fill, problemText, useLang, useT } from '../i18n'
import { api, ApiError, type SupplierPage } from '../lib/api'
import { amd, tr } from '../lib/format'
import { pendingInvite } from '../lib/invite'
import { useSession } from '../lib/session'
import { Catalog } from './Catalog'

/**
 * A supplier's own page behind its standing link /s/<code>: who they are, how they deliver, their products.
 * Visitors who sign up from here become the supplier's clients with no Easo commission.
 */
export function Supplier() {
  const t = useT()
  const lang = useLang()
  const { code = '' } = useParams()
  const { me, org, mode } = useSession()
  const [page, setPage] = useState<SupplierPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [joined, setJoined] = useState(false)
  const hasCompany = !!org

  useEffect(() => {
    setPage(null)
    setError(null)
    api.supplier(code).then(
      (p) => {
        setPage(p)
        // Remembered for sign-in and sign-up only; a registered company joins with one tap here
        if (hasCompany || p.client) pendingInvite.clear()
        else pendingInvite.set(p.code)
      },
      (e: ApiError) => setError(problemText(e.code, lang)),
    )
    // Signing in changes whether the visitor is already a client
  }, [code, lang, me?.id, hasCompany])

  if (error) return <p className="error page">{error}</p>
  if (!page) return <p className="muted page">{t('loading')}</p>

  const own = org?.id === page.seller_id
  const client = page.client || joined
  const about = page.description ? tr(page.description, lang) : ''

  async function join() {
    setBusy(true)
    setError(null)
    try {
      await api.joinSupplier(page!.code)
      pendingInvite.clear()
      setJoined(true)
    } catch (e) {
      setError(problemText((e as ApiError).code, lang))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack page supplier">
      <section className="ticket supplier-hero">
        <p className="eyebrow">{t('sup_eyebrow')}</p>
        <h1>{page.display_name}</h1>
        {about && <p className="supplier-about">{about}</p>}
        <dl className="supplier-facts">
          <div>
            <dt>{t('sup_min_order')}</dt>
            <dd className="num">{page.min_order_amd ? amd(page.min_order_amd) : '—'}</dd>
          </div>
          <div>
            <dt>{t('sup_delivery')}</dt>
            <dd className="num">
              {page.delivery_fee_amd ? amd(page.delivery_fee_amd) : t('sup_delivery_free')}
              {page.delivery_fee_amd > 0 && page.free_delivery_from_amd != null && (
                <small>
                  {t('sup_free_from')} {amd(page.free_delivery_from_amd)}
                </small>
              )}
            </dd>
          </div>
          <div>
            <dt>{t('sup_when')}</dt>
            <dd>{fill(t('sup_lead'), { time: page.cutoff_time.slice(0, 5), days: page.lead_days })}</dd>
          </div>
          {page.districts.length > 0 && (
            <div className="wide">
              <dt>{t('sup_districts')}</dt>
              <dd>{page.districts.join(', ')}</dd>
            </div>
          )}
        </dl>
      </section>

      {own ? (
        <p className="pill plain self-start">{t('sup_own')}</p>
      ) : client ? (
        <div className="next calm">
          <div>
            <strong>{t('sup_client')}</strong>
            {joined && <span className="muted">{fill(t('sup_joined'), { name: page.display_name })}</span>}
          </div>
          <span className="clock">✓</span>
        </div>
      ) : (
        <div className="next supplier-join">
          <div>
            <strong>{t('sup_join_title')}</strong>
            <span className="muted">{t('sup_join_text')}</span>
          </div>
          {!me ? (
            <Link className="clock" to="/login">
              {t('sup_sign_in')} →
            </Link>
          ) : !org ? (
            <Link className="clock" to="/onboarding">
              {t('sup_register')} →
            </Link>
          ) : org.is_buyer && mode === 'buyer' ? (
            <button className="clock" disabled={busy} onClick={join}>
              {busy ? t('loading') : `${t('sup_join')} →`}
            </button>
          ) : null}
        </div>
      )}

      {page.verified ? (
        <>
          <h2 className="section-title">
            {t('sup_products')} · <span className="num">{page.products}</span>
          </h2>
          <Catalog canBuy={!own && (!org || mode === 'buyer')} invite={page.code} categoryIds={page.category_ids} />
        </>
      ) : (
        <p className="muted">{t('sup_pending')}</p>
      )}
    </div>
  )
}
