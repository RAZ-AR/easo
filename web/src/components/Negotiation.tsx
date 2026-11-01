// The signature of Easo: haggling shown as two prices converging on one line.
import { useT } from '../i18n'
import type { Offer, Order, OrderLine } from '../lib/api'
import { amd } from '../lib/format'

interface Point {
  value: number
  label: string
}

/**
 * One track per negotiated item. The list price sits at the right edge, the buyer's offers below the line,
 * the seller's counters above it; older rounds fade, the gap between the latest two glows amber
 * and turns green when they meet.
 */
export function ConvergenceTrack({
  title,
  list,
  buyer,
  seller,
  deal,
  fmt,
  mySide,
}: {
  title: string
  list: number
  buyer: Point[]
  seller: Point[]
  deal: number | null
  fmt: (v: number) => string
  mySide: 'buyer' | 'seller'
}) {
  const t = useT()
  const values = [list, ...buyer.map((p) => p.value), ...seller.map((p) => p.value)]
  const lo = Math.min(...values)
  const span = Math.max(1, list - lo)
  // 6%..94%: keep labels inside the card
  const x = (v: number) => `${6 + ((v - lo) / span) * 88}%`
  const lastB = buyer.at(-1)
  const lastS = seller.at(-1)
  const gapFrom = lastB?.value ?? lo
  const gapTo = deal ?? lastS?.value ?? list
  const youAre = mySide === 'buyer' ? 'buyer' : 'seller'
  return (
    <div className="conv">
      <div className="conv-head">
        <span>{title}</span>
        {deal !== null ? (
          <span className="pill saving">
            {t('agreed_at')} {fmt(deal)}
          </span>
        ) : lastB && lastS ? (
          <span className="pill lamp num">Δ {fmt(Math.abs(lastS.value - lastB.value))}</span>
        ) : null}
      </div>
      <div className="conv-track" role="img" aria-label={title}>
        <div
          className={deal !== null ? 'conv-gap closed' : 'conv-gap'}
          style={{ left: x(Math.min(gapFrom, gapTo)), width: `calc(${x(Math.max(gapFrom, gapTo))} - ${x(Math.min(gapFrom, gapTo))})` }}
        />
        <div className="mk list" style={{ left: x(list) }}>
          <i />
        </div>
        {buyer.map((p, i) => (
          <div key={`b${i}`} className={i === buyer.length - 1 && deal === null ? 'mk buyer' : 'mk buyer ghost'} style={{ left: x(p.value) }}>
            <i />
            <span>{p.label}</span>
          </div>
        ))}
        {seller.map((p, i) => (
          <div key={`s${i}`} className={i === seller.length - 1 && deal === null ? 'mk seller' : 'mk seller ghost'} style={{ left: x(p.value) }}>
            <span>{p.label}</span>
            <i />
          </div>
        ))}
        {deal !== null && (
          <div className="mk deal" style={{ left: x(deal) }}>
            <span>{fmt(deal)}</span>
            <i />
          </div>
        )}
      </div>
      <div className="conv-legend">
        <span style={{ color: 'var(--brand)' }}>● {youAre === 'buyer' ? t('you') : t('them_buyer')}</span>
        <span style={{ color: 'var(--seller)' }}>● {youAre === 'seller' ? t('you') : t('them_seller')}</span>
        <span>
          {t('list_price')} {fmt(list)}
        </span>
      </div>
    </div>
  )
}

/** Build tracks from the order's offers: one per line, or one for an order-level discount. */
export function OrderConvergence({ order, lineName }: { order: Order; lineName: (l: OrderLine) => string }) {
  const nego = order.negotiation
  if (!nego) return null
  const offers: Offer[] = nego.offers
  if (nego.scope === 'order') {
    // Discount in %: smaller discount = closer to the list price, so plot −% as the value
    const pts = (side: 'buyer' | 'seller') =>
      offers.filter((o) => o.from_side === side).map((o) => ({ value: -(o.percent_bp ?? 0), label: `−${(o.percent_bp ?? 0) / 100}%` }))
    const accepted = offers.find((o) => o.response === 'accepted')
    return (
      <ConvergenceTrack
        title="%"
        list={0}
        buyer={pts('buyer')}
        seller={pts('seller')}
        deal={accepted ? -(accepted.percent_bp ?? 0) : null}
        fmt={(v) => `${v === 0 ? '0' : `−${-v / 100}`}%`}
        mySide={order.my_side}
      />
    )
  }
  const lineIds = [...new Set(offers.flatMap((o) => o.lines.map((l) => l.order_line_id)))]
  return (
    <>
      {lineIds.map((id) => {
        const line = order.lines.find((l) => l.id === id)
        if (!line) return null
        const pts = (side: 'buyer' | 'seller') =>
          offers
            .filter((o) => o.from_side === side)
            .flatMap((o) => o.lines.filter((ol) => ol.order_line_id === id))
            .map((ol) => ({ value: ol.proposed_price_amd, label: amd(ol.proposed_price_amd) }))
        const deal = line.price_source === 'negotiated' ? line.final_price_amd : null
        return (
          <ConvergenceTrack
            key={id}
            title={lineName(line)}
            list={line.list_price_amd}
            buyer={pts('buyer')}
            seller={pts('seller')}
            deal={deal}
            fmt={amd}
            mySide={order.my_side}
          />
        )
      })}
    </>
  )
}
