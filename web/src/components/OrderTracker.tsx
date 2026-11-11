// Delivery-app style tracker: a line of status icons, the current one lit, and one sentence on what happens now.
import type React from 'react'
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { fill, useLang, useT, type TextKey } from '../i18n'
import { api, type OrderListItem, type OrderStatus } from '../lib/api'
import { date, slot } from '../lib/format'
import { Icon } from './ui'

/** Statuses the buyer is still waiting on */
export const LIVE: OrderStatus[] = ['negotiating', 'placed', 'confirmed', 'delivering', 'delivered']
const POLL_MS = 20_000

type Step = 'negotiating' | 'placed' | 'confirmed' | 'delivering' | 'delivered'
const ICONS: Record<Step, () => React.ReactNode> = {
  negotiating: Icon.haggle,
  placed: Icon.ticket,
  confirmed: Icon.check,
  delivering: Icon.truck,
  delivered: Icon.box,
}
const CAPTION: Record<Step, TextKey> = {
  negotiating: 'track_negotiating',
  placed: 'track_placed',
  confirmed: 'track_confirmed',
  delivering: 'track_delivering',
  delivered: 'track_delivered',
}

export interface Trackable {
  id: string
  status: OrderStatus
  seller: string
  delivery_date: string | null
  slot_from: string | null
  slot_to: string | null
  express: boolean
  negotiated?: boolean // an offer was made, so the "offer" step stays on the line
}

export function OrderTracker({ o, link = true }: { o: Trackable; link?: boolean }) {
  const t = useT()
  const lang = useLang()
  const steps: Step[] = [
    ...(o.status === 'negotiating' || o.negotiated ? (['negotiating'] as Step[]) : []),
    'placed',
    'confirmed',
    'delivering',
    'delivered',
  ]
  const current = (LIVE.includes(o.status) ? o.status : 'delivered') as Step
  const idx = steps.indexOf(current)
  const when = o.express
    ? t('track_today')
    : o.delivery_date
      ? date(o.delivery_date, lang) + (o.slot_from && o.slot_to ? `, ${slot(o.slot_from, o.slot_to)}` : '')
      : ''
  const body = (
    <>
      <div className="tracker-head">
        <b>{o.seller}</b>
        {o.express && <span className="pill express">⚡ {t('express_badge')}</span>}
      </div>
      <ol className="tracker-line" style={{ '--progress': idx / Math.max(1, steps.length - 1) } as React.CSSProperties}>
        {steps.map((s, i) => (
          <li key={s} className={i < idx ? 'done' : i === idx ? 'now' : ''} aria-current={i === idx ? 'step' : undefined}>
            {ICONS[s]()}
          </li>
        ))}
      </ol>
      <p className="tracker-caption">{fill(t(CAPTION[current]), { when })}</p>
    </>
  )
  return link ? (
    <Link to={`/orders/${o.id}`} className="tracker" aria-live="polite">
      {body}
    </Link>
  ) : (
    <section className="tracker" aria-live="polite">
      {body}
    </section>
  )
}

export function fromListItem(o: OrderListItem): Trackable {
  return {
    id: o.id,
    status: o.status,
    seller: o.counterparty.display_name,
    delivery_date: o.delivery_date,
    slot_from: o.slot_from,
    slot_to: o.slot_to,
    express: o.express,
  }
}

/** The buyer's orders in progress, on top of the catalog (the order page draws its own). */
export function ActiveOrders() {
  const { pathname } = useLocation()
  const [items, setItems] = useState<OrderListItem[]>([])
  const hidden = pathname !== '/'
  useEffect(() => {
    if (hidden) return
    let alive = true
    const load = () =>
      api.orders(LIVE).then(
        (r) => alive && setItems(r.items.filter((o) => o.my_side === 'buyer')),
        () => {},
      )
    void load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [hidden, pathname])
  if (hidden || items.length === 0) return null
  return (
    <div className={items.length > 1 ? 'trackers many' : 'trackers'}>
      {items.map((o) => (
        <OrderTracker key={o.id} o={fromListItem(o)} />
      ))}
    </div>
  )
}
