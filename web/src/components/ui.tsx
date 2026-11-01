// Small shared pieces of the Easo visual language: icons, product tiles, countdowns, the order-minimum meter.
import { useEffect, useState } from 'react'
import { useT } from '../i18n'
import { amd } from '../lib/format'

const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

export const Icon = {
  catalog: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="2" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="2" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="2" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="2" />
    </svg>
  ),
  cart: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M3 4h2l2.2 10.2a2 2 0 0 0 2 1.6h7.6a2 2 0 0 0 2-1.5L20.5 8H6" />
      <circle cx="10" cy="20" r="1.3" />
      <circle cx="17" cy="20" r="1.3" />
    </svg>
  ),
  // a kitchen ticket: orders
  ticket: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M6 3h12v18l-2-1.5L14 21l-2-1.5L10 21l-2-1.5L6 21z" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </svg>
  ),
  // a building: the company profile
  company: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M4 20V6l8-3v17M12 9h8v11M4 20h17" />
      <path d="M7.5 9h1M7.5 12.5h1M7.5 16h1M15.5 12.5h1M15.5 16h1" />
    </svg>
  ),
  user: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6" />
    </svg>
  ),
  logout: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l-4-4 4-4M6 12h10" />
    </svg>
  ),
  bell: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5a2.2 2.2 0 0 0 4 0" />
    </svg>
  ),
  doc: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5" />
    </svg>
  ),
  truck: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M3 6h11v10H3zM14 9h4l3 3v4h-7" />
      <circle cx="7" cy="17.5" r="1.8" />
      <circle cx="17" cy="17.5" r="1.8" />
    </svg>
  ),
  // two people: the seller's clients
  people: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <circle cx="9" cy="8.5" r="3.2" />
      <path d="M3 19.5c.9-3.2 3.2-5 6-5s5.1 1.8 6 5" />
      <path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.5 14.8c1.7.6 2.9 2.2 3.5 4.7" />
    </svg>
  ),
  inbox: () => (
    <svg viewBox="0 0 24 24" {...stroke}>
      <path d="M4 13l2.5-8h11L20 13v6H4z" />
      <path d="M4 13h4.5l1 2h5l1-2H20" />
    </svg>
  ),
}

// Category tint: one stable hue per category, so a chef learns the shelf by colour
const HUES: Record<string, number> = {
  disposables: 210, cleaning: 190, grocery: 35, dairy: 48, meat: 0, produce: 120, coffee: 25, bakery: 30,
}

export function Tile({ slug, name }: { slug?: string; name: string }) {
  const hue = slug && slug in HUES ? HUES[slug] : 30
  return (
    <div className="tile" style={{ background: `hsl(${hue} 55% 88%)`, color: `hsl(${hue} 45% 28%)` }} aria-hidden>
      {name.trim().charAt(0).toUpperCase()}
    </div>
  )
}

/** Re-render every `ms` so countdowns stay true without a server push. */
export function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

export function Countdown({ until }: { until: string }) {
  const t = useT()
  const now = useNow()
  const left = new Date(until).getTime() - now
  if (left <= 0) return <span className="clock">{t('overdue')}</span>
  const h = Math.floor(left / 3_600_000)
  const m = Math.floor((left % 3_600_000) / 60_000)
  return (
    <span className="clock">
      {h > 0 ? `${h} ${t('h')} ` : ''}
      {m} {t('m')}
    </span>
  )
}

/**
 * Progress towards the seller's minimum order and free delivery, on one track.
 * Amber until the minimum is reached, green after; the free-delivery threshold is a tick on the track.
 */
export function OrderMeter({
  subtotal,
  min,
  freeFrom,
}: {
  subtotal: number
  min: number
  freeFrom: number | null
}) {
  const t = useT()
  const end = Math.max(freeFrom ?? 0, min, subtotal, 1)
  const pct = (x: number) => `${Math.min(100, (x / end) * 100)}%`
  const belowMin = subtotal < min
  const toFree = freeFrom !== null ? freeFrom - subtotal : null
  return (
    <div className="meter" aria-label={t('to_min')}>
      <div className="meter-track">
        <div className={belowMin ? 'meter-fill' : 'meter-fill ok'} style={{ width: pct(subtotal) }} />
        {min > 0 && <div className="meter-mark" style={{ left: pct(min) }} title={`${t('min_order')} ${amd(min)}`} />}
        {freeFrom !== null && <div className="meter-mark" style={{ left: `calc(${pct(freeFrom)} - 2px)` }} />}
      </div>
      <div className="meter-legend">
        {belowMin ? (
          <span className="lamp">
            {t('to_min')}: {t('more')} {amd(min - subtotal)}
          </span>
        ) : toFree !== null && toFree > 0 ? (
          <span>
            {t('to_free')}: {t('more')} <b className="num">{amd(toFree)}</b>
          </span>
        ) : (
          <span className="herb">✓ {freeFrom !== null ? t('free_reached') : ''}</span>
        )}
        <span className="muted num">
          {t('min_order')} {amd(min)}
        </span>
      </div>
    </div>
  )
}
