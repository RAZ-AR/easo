import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Countdown } from '../components/ui'
import { STATUSES, STEPS, useLang, useT, type TextKey } from '../i18n'
import { api, type NextStep, type OrderListItem } from '../lib/api'
import { amd, date, slot, tr } from '../lib/format'

// Until a notification channel is chosen, lists refresh themselves
const POLL_MS = 15_000
const URGENT: NextStep[] = ['answer_offer', 'answer_counter', 'accept_delivery']

function useOrders(): { items: OrderListItem[] | null; error: string | null } {
  const [items, setItems] = useState<OrderListItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () =>
      api.orders().then(
        (page) => alive && setItems(page.items),
        (e: Error) => alive && setError(e.message),
      )
    void load()
    const id = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])
  return { items, error }
}

export function Orders({ inbox = false }: { inbox?: boolean }) {
  const t = useT()
  const location = useLocation()
  const { items, error } = useOrders()
  const sent = (location.state as { sent?: boolean } | null)?.sent

  if (error) return <p className="error">{error}</p>
  if (!items) return <p className="muted">{t('loading')}</p>
  if (inbox) return <SellerBoard items={items} />

  const needs = items.filter((o) => o.next_step)
  const done = items.filter((o) => !o.next_step && ['accepted', 'cancelled', 'rejected'].includes(o.status))
  const active = items.filter((o) => !needs.includes(o) && !done.includes(o))

  return (
    <div className="stack page">
      <h1 className="page-title">{t('tab_orders')}</h1>
      {sent && <p className="notice">✓ {t('sent')}</p>}
      {items.length === 0 && (
        <div className="empty">
          <div className="big">🧾</div>
          <p>{t('orders_empty')}</p>
        </div>
      )}
      <Section title="sec_action" items={needs} />
      <Section title="sec_active" items={active} />
      <Section title="sec_done" items={done} />
    </div>
  )
}

function Section({ title, items }: { title: TextKey; items: OrderListItem[] }) {
  const t = useT()
  if (items.length === 0) return null
  return (
    <section className="column">
      <h2 className="section-title">
        {t(title)} · {items.length}
      </h2>
      <ul className="order-list">
        {items.map((o) => (
          <OrderCard key={o.id} o={o} />
        ))}
      </ul>
    </section>
  )
}

function OrderCard({ o }: { o: OrderListItem }) {
  const t = useT()
  const lang = useLang()
  const urgent = o.next_step !== null && URGENT.includes(o.next_step)
  const more = o.lines_count - o.preview.length
  return (
    <li>
      <Link to={`/orders/${o.id}`} className={`order-card${urgent ? ' urgent' : o.next_step ? ' need' : ''}`}>
        <span className="ibtn" aria-hidden>
          ↗
        </span>
        <div className="top">
          <strong>{o.counterparty.display_name}</strong>
          <span className="sum num">{amd(o.total_amd)}</span>
        </div>
        <div className="names">
          {o.preview.map((n) => tr(n, lang)).join(', ')}
          {more > 0 && ` +${more}`}
        </div>
        <div className="bottom">
          <span className="meta" style={{ margin: 0 }}>
            <span className={`status s-${o.status}`}>{STATUSES[o.status][lang]}</span>
            {o.delivery_date && (
            <span className={o.express ? 'pill express' : 'pill plain'}>
              {o.express ? `⚡ ${t('express_badge')}` : '🚚'} {date(o.delivery_date, lang)}
              {o.slot_from && o.slot_to && `, ${slot(o.slot_from, o.slot_to)}`}
            </span>
          )}
            {o.saving_amd > 0 && <span className="pill saving num">−{amd(o.saving_amd)}</span>}
            {o.payment_status === 'paid' && <span className="pill saving">✓ {t('paid')}</span>}
          </span>
          {o.next_step && (
            <span className={urgent ? 'pill lamp' : 'pill brand'}>
              {STEPS[o.next_step][lang]}
              {o.deadline && (
                <>
                  {' · '}
                  <Countdown until={o.deadline} />
                </>
              )}
            </span>
          )}
        </div>
        <span className="muted small mono">{o.number}</span>
      </Link>
    </li>
  )
}

/** Seller inbox as a triage board: what needs an answer first, then the day's work, then money to collect. */
function SellerBoard({ items }: { items: OrderListItem[] }) {
  const t = useT()
  const today = new Date().toDateString()
  const todays = items.filter((o) => new Date(o.created_at).toDateString() === today && o.status !== 'cancelled')
  const columns: { key: TextKey; step: NextStep }[] = [
    { key: 'sec_answer', step: 'answer_offer' },
    { key: 'sec_confirm', step: 'confirm' },
    { key: 'sec_deliver', step: 'deliver' },
    { key: 'sec_paid', step: 'mark_paid' },
  ]
  const by = (step: NextStep) =>
    items
      .filter((o) => o.next_step === step)
      .sort((a, b) => (a.deadline ?? a.created_at).localeCompare(b.deadline ?? b.created_at))
  const waiting = items.filter((o) => o.next_step === 'answer_offer' || o.next_step === 'confirm').length

  return (
    <div className="stack page" style={{ maxWidth: 1120 }}>
      <h1 className="page-title">{t('tab_inbox')}</h1>
      <div className="kpis">
        <div className="kpi">
          <b>{todays.length}</b>
          <span>{t('today_orders')}</span>
        </div>
        <div className="kpi dark">
          <b className="num">{amd(todays.reduce((s, o) => s + o.total_amd, 0))}</b>
          <span>{t('turnover')}</span>
        </div>
        <div className={waiting ? 'kpi lamp' : 'kpi'}>
          <b>{waiting}</b>
          <span>{t('waiting_you')}</span>
        </div>
      </div>
      {items.every((o) => !o.next_step) && <p className="empty">✓ {t('inbox_empty')}</p>}
      <div className="board rail-board">
        {columns.map(({ key, step }) => {
          const list = by(step)
          return (
            <section key={step} className="column">
              <div className="column-head">
                <h2 className="section-title">{t(key)}</h2>
                <span className="pill plain">{list.length}</span>
              </div>
              {list.length === 0 ? (
                <p className="col-empty">✓</p>
              ) : (
                <ul className="order-list">
                  {list.map((o) => (
                    <OrderCard key={o.id} o={o} />
                  ))}
                </ul>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
