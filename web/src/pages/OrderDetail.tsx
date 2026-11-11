import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { OrderConvergence } from '../components/Negotiation'
import { OrderChat } from '../components/Chat'
import { ClientCard } from '../components/ClientCard'
import { OrderDocuments } from '../components/OrderDocuments'
import { PriceDial } from '../components/PriceDial'
import { Sheet } from '../components/Sheet'
import { LIVE, OrderTracker } from '../components/OrderTracker'
import { StageRail } from '../components/StageRail'
import { Countdown } from '../components/ui'
import { ACTIONS, problemText, SOURCES, STATUSES, STEPS, TEXTS, useLang, useT, type TextKey } from '../i18n'
import { api, ApiError, type Decision, type LineAnswer, type Offer, type Order, type OrderLine } from '../lib/api'
import { useCartMaybe } from '../lib/cart'
import { amd, date, dateTime, qty, slot, tr, unit } from '../lib/format'
import { midpoint, pct } from '../lib/haggle'
import { useToast } from '../lib/toast'

const POLL_MS = 10_000
// Which button carries the next step (drawn as the primary one)
const STEP_ACTION: Record<string, string> = {
  confirm: 'confirm',
  deliver: 'deliver',
  accept_delivery: 'accept',
  mark_paid: 'mark_paid',
}

/** Reasons set by the system; anything else is the text the other side typed */
const SYSTEM_REASONS = ['offer_expired', 'buyer', 'seller_rejected']

export function OrderDetail() {
  const { id = '' } = useParams()
  const t = useT()
  const lang = useLang()
  const navigate = useNavigate()
  const cart = useCartMaybe()
  const [order, setOrder] = useState<Order | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmQty, setConfirmQty] = useState<Record<string, string>>({})
  // Cancelling and rejecting cannot be undone: ask first, with an optional reason for the other side
  const [asking, setAsking] = useState<'cancel' | 'reject' | null>(null)
  const [reason, setReason] = useState('')
  const toast = useToast()

  const load = useCallback(() => api.order(id).then(setOrder, (e: Error) => setError(e.message)), [id])

  useEffect(() => {
    void load()
    const timer = setInterval(load, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  async function run<T>(p: Promise<T>, after?: (r: T) => void) {
    setBusy(true)
    setError(null)
    try {
      const r = await p
      after?.(r)
      navigator.vibrate?.(10)
    } catch (e) {
      const err = e as ApiError
      setError(err.problems?.length ? err.problems.map((x) => problemText(x.code, lang)).join(' · ') : problemText(err.code, lang))
    } finally {
      setBusy(false)
    }
  }

  if (!order) return error ? <p className="error">{error}</p> : <p className="muted">{t('loading')}</p>

  const isSeller = order.my_side === 'seller'
  const other = isSeller ? order.buyer : order.seller
  const nego = order.negotiation
  const primary = order.next_step ? STEP_ACTION[order.next_step] : undefined

  function doAction(action: string, confirmed = false) {
    if (!order) return
    const done = (o: Order) => {
      setOrder(o)
      toast(t(`done_${action}` as TextKey))
    }
    if (action === 'confirm') {
      const lines = Object.entries(confirmQty).map(([order_line_id, q]) => ({ order_line_id, qty: q || '0' }))
      return run(api.confirm(order.id, lines), done)
    }
    if (action === 'accept') return run(api.accept(order.id), done)
    if (action === 'reorder')
      return run(api.reorder(order.id), async () => {
        await cart?.refresh()
        toast(t('done_reorder'))
        navigate('/cart')
      })
    if ((action === 'cancel' || action === 'reject') && !confirmed) {
      setReason('')
      return setAsking(action)
    }
    setAsking(null)
    const a = action as 'cancel' | 'reject' | 'ship' | 'deliver' | 'mark_paid'
    return run(api.action(order.id, a, reason.trim() || undefined), done)
  }

  const actions = order.allowed_actions
    .filter((a) => a !== 'respond')
    .sort((a, b) => Number(b === primary) - Number(a === primary))

  return (
    <div className="stack page">
      <Link to={isSeller ? '/' : '/orders'} className="link">
        ← {t('back')}
      </Link>
      {!isSeller && LIVE.includes(order.status) && (
        <OrderTracker
          link={false}
          o={{
            id: order.id,
            status: order.status,
            seller: order.seller.display_name,
            delivery_date: order.delivery_date,
            slot_from: order.slot_from,
            slot_to: order.slot_to,
            express: order.express,
            negotiated: !!order.negotiation,
          }}
        />
      )}

      <section className="ticket perf">
        <header className="ticket-head">
          <div>
            <h1>{other.display_name}</h1>
            <p className="muted small" style={{ margin: '4px 0 0' }}>
              <span className="mono">{order.number}</span> · {dateTime(order.created_at, lang)}
            </p>
          </div>
          <span className={`status s-${order.status}`}>{STATUSES[order.status][lang]}</span>
        </header>
        <div className="meta" style={{ marginTop: 10 }}>
          {order.delivery_date && (
            <span className={order.express ? 'pill express' : 'pill plain'}>
              {order.express ? `⚡ ${t('express_badge')}` : '🚚'} {date(order.delivery_date, lang)}
              {order.slot_from && order.slot_to && `, ${slot(order.slot_from, order.slot_to)}`}
            </span>
          )}
          {order.address && <span className="pill plain">📍 {order.address.text}</span>}
          {order.address && (order.address.contact_name || order.address.contact_phone) && (
            <span className="pill plain">
              👤 {[order.address.contact_name, order.address.contact_phone].filter(Boolean).join(', ')}
            </span>
          )}
          {order.address?.delivery_notes && <span className="pill plain">💬 {order.address.delivery_notes}</span>}
          <span className={order.payment_status === 'paid' ? 'pill saving' : 'pill plain'}>
            {order.payment_status === 'paid' ? `✓ ${t('paid')}` : t('unpaid')}
          </span>
        </div>
        {(isSeller || !LIVE.includes(order.status)) && <StageRail order={order} />}
      </section>

      {order.next_step && (
        <div className="next">
          <div>
            <strong>{STEPS[order.next_step][lang]}</strong>
            {order.next_step === 'answer_offer' || order.next_step === 'answer_counter' ? (
              <span className="muted small">
                {t('rounds_left')}: {nego?.rounds_left}
              </span>
            ) : null}
          </div>
          {order.deadline && <Countdown until={order.deadline} />}
        </div>
      )}
      {!order.next_step && nego?.status.startsWith('awaiting') && order.status === 'negotiating' && (
        <div className="next calm">
          <span>
            {t('offer_sent_hint')} {dateTime(nego.expires_at, lang)}
          </span>
          <Countdown until={nego.expires_at} />
        </div>
      )}
      {order.cancel_reason === 'offer_expired' && <p className="warn">{TEXTS.cancel_reason_offer_expired[lang]}</p>}
      {order.cancel_reason && !SYSTEM_REASONS.includes(order.cancel_reason) && (
        <p className="warn">
          {t('reason')}: {order.cancel_reason}
        </p>
      )}
      {notice && <p className="notice">{notice}</p>}

      {nego && (
        <section className="ticket">
          <header className="ticket-head">
            <h2>⇄ {t('negotiation')}</h2>
            {order.saving_amd > 0 && (
              <span className="pill saving num">
                {isSeller ? t('buyer_discount') : t('saving')} {amd(order.saving_amd)}
              </span>
            )}
          </header>
          {isSeller && order.client && <ClientCard name={order.buyer.display_name} c={order.client} />}
          <OrderConvergence order={order} lineName={(l) => tr(l.name_snapshot, lang)} />
          <OfferLog offers={nego.offers} order={order} />
          {nego.my_turn && order.status === 'negotiating' && (
            <AnswerBox order={order} busy={busy} onAnswer={(body) => run(api.respond(order.id, body), setOrder)} />
          )}
        </section>
      )}

      {!nego && isSeller && order.client && order.status === 'placed' && (
        <section className="ticket">
          <ClientCard name={order.buyer.display_name} c={order.client} />
        </section>
      )}

      <section className="ticket">
        <ul className="lines">
          {order.lines.map((l) => (
            <OrderLineRow
              key={l.id}
              l={l}
              editable={isSeller && order.allowed_actions.includes('confirm')}
              value={confirmQty[l.id]}
              onChange={(v) => setConfirmQty((c) => ({ ...c, [l.id]: v }))}
              canFix={isSeller && l.price_source === 'negotiated' && !['cancelled', 'rejected'].includes(order.status)}
              onFix={() => run(api.createAgreement(l.id), () => setNotice(t('price_fixed')))}
            />
          ))}
        </ul>
        <hr className="rule" />
        <dl className="sums">
          <dt>{t('subtotal')}</dt>
          <dd>{amd(order.subtotal_amd)}</dd>
          {order.discount_amd > 0 && (
            <>
              <dt>{t('discount')}</dt>
              <dd className="herb">−{amd(order.discount_amd)}</dd>
            </>
          )}
          <dt>{t('delivery')}</dt>
          <dd>{order.delivery_fee_amd ? amd(order.delivery_fee_amd) : t('free')}</dd>
          <dt className="strong">{t('total')}</dt>
          <dd className="strong">{amd(order.total_amd)}</dd>
          {order.saving_amd > 0 && (
            <>
              <dt>{isSeller ? t('buyer_discount') : t('saving')}</dt>
              <dd className="herb">{amd(order.saving_amd)}</dd>
            </>
          )}
        </dl>
      </section>

      <div id="chat">
        <OrderChat order={order} />
      </div>

      <OrderDocuments order={order} />

      {asking && (
        <Sheet onClose={() => setAsking(null)}>
          <form
            className="sheet-form"
            onSubmit={(e) => {
              e.preventDefault()
              void doAction(asking, true)
            }}
          >
            <h2 style={{ margin: 0 }}>{t(asking === 'cancel' ? 'confirm_cancel_title' : 'confirm_reject_title')}</h2>
            <p className="muted" style={{ margin: 0 }}>
              {order.number} · {other.display_name} · {amd(order.total_amd)}
            </p>
            <p style={{ margin: 0 }}>{t('confirm_text')}</p>
            <label className="field">
              <span>{t('reason_optional')}</span>
              <input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="actions">
              <button type="button" className="btn" onClick={() => setAsking(null)}>
                {t('keep_order')}
              </button>
              <button className="btn danger">{ACTIONS[asking][lang]}</button>
            </div>
          </form>
        </Sheet>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {actions.length > 0 && (
        <div className="actions">
          {actions.map((a) => (
            <button
              key={a}
              className={a === primary ? 'btn primary' : ['cancel', 'reject'].includes(a) ? 'btn ghost' : 'btn'}
              disabled={busy}
              onClick={() => doAction(a)}
            >
              {ACTIONS[a]?.[lang] ?? a}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function OrderLineRow({
  l,
  editable,
  value,
  onChange,
  canFix,
  onFix,
}: {
  l: OrderLine
  editable: boolean
  value: string | undefined
  onChange: (v: string) => void
  canFix: boolean
  onFix: () => void
}) {
  const t = useT()
  const lang = useLang()
  const price = l.final_price_amd ?? l.list_price_amd
  const shown = l.qty_confirmed ?? l.qty_ordered
  const changed = l.qty_confirmed !== null && Number(l.qty_confirmed) !== Number(l.qty_ordered)
  return (
    <li className="line">
      <div className="line-main">
        <div>
          <div className="line-name">{tr(l.name_snapshot, lang)}</div>
          <div className="line-sub num">
            {l.final_price_amd !== null && l.final_price_amd !== l.list_price_amd && <span className="strike">{amd(l.list_price_amd)}</span>}{' '}
            {amd(price)} / {unit(l.unit, lang)}
            {l.price_source !== 'list' && <span style={{ color: 'var(--herb)' }}> · {SOURCES[l.price_source][lang]}</span>}
            {l.final_price_amd === null && <span style={{ color: 'var(--lamp)' }}> · {t('negotiation')}</span>}
          </div>
        </div>
        {editable ? (
          <label className="qty-edit">
            <span className="muted small">
              {t('confirmed_qty')}, {unit(l.unit, lang)}
            </span>
            <input inputMode="decimal" value={value ?? qty(l.qty_ordered)} onChange={(e) => onChange(e.target.value.replace(',', '.'))} />
          </label>
        ) : (
          <div className="line-total num">
            {l.line_total_amd !== null ? amd(l.line_total_amd) : '—'}
            <div className="muted small" style={{ fontWeight: 500 }}>
              {changed && <span className="strike">{qty(l.qty_ordered)}</span>} {qty(shown)} {unit(l.unit, lang)}
            </div>
          </div>
        )}
      </div>
      {canFix && (
        <button className="link small" onClick={onFix}>
          📌 {t('fix_price')}
        </button>
      )}
    </li>
  )
}

function OfferLog({ offers, order }: { offers: Offer[]; order: Order }) {
  const t = useT()
  const lang = useLang()
  const names = Object.fromEntries(order.lines.map((l) => [l.id, l]))
  const who = (o: Offer) =>
    o.is_auto ? t('autopilot') : o.from_side === order.my_side ? t('you') : o.from_side === 'buyer' ? t('them_buyer') : t('them_seller')
  return (
    <ol className="offers">
      {offers.map((o) => (
        <li key={o.id} className={`offer from-${o.from_side}`}>
          <span>
            <b>{who(o)}</b>:{' '}
            {o.kind === 'percent'
              ? `−${(o.percent_bp ?? 0) / 100}%`
              : o.lines.map((ol) => `${names[ol.order_line_id] ? tr(names[ol.order_line_id].name_snapshot, lang) : ''} ${amd(ol.proposed_price_amd)}`).join(', ')}
          </span>
          <span className="muted">
            {o.response && <span className={`d-${o.response}`}>{responseText(o.response, lang)} · </span>}
            {dateTime(o.created_at, lang)}
          </span>
        </li>
      ))}
    </ol>
  )
}

function AnswerBox({
  order,
  busy,
  onAnswer,
}: {
  order: Order
  busy: boolean
  onAnswer: (body: { lines?: LineAnswer[]; order?: { decision: Decision; percent_bp?: number } }) => void
}) {
  const t = useT()
  const lang = useLang()
  const nego = order.negotiation!
  const open = nego.offers.find((o) => o.response === null)
  const names = Object.fromEntries(order.lines.map((l) => [l.id, l]))
  const [answers, setAnswers] = useState<Record<string, { decision: Decision; price: number | null }>>({})
  const [percent, setPercent] = useState<number | null>(null)
  const [mode, setMode] = useState<Decision>('accepted')
  if (!open) return null
  const canCounter = nego.rounds_left > 0

  // Bounds for a counter: prices must converge without crossing
  const lastOwn = (lineId: string) =>
    nego.offers
      .filter((o) => o.from_side === order.my_side)
      .flatMap((o) => o.lines)
      .filter((ol) => ol.order_line_id === lineId)
      .at(-1)?.proposed_price_amd

  if (nego.scope === 'order') {
    const theirs = open.percent_bp ?? 0
    const own = nego.offers.filter((o) => o.from_side === order.my_side).at(-1)?.percent_bp
    // seller counters with a smaller discount, buyer with a bigger one (but below their previous ask)
    const lo = order.my_side === 'seller' ? 50 : theirs + 50
    const hi = order.my_side === 'seller' ? theirs - 50 : (own ?? 5000) - 50
    // Halfway between the two latest asks, in half-percent steps
    const half = Math.round(((theirs + (own ?? 0)) / 2) / 50) * 50
    return (
      <div className="answer">
        <div className="seg">
          {(['accepted', 'countered', 'declined'] as Decision[])
            .filter((d) => d !== 'countered' || (canCounter && hi >= lo))
            .map((d) => (
              <button key={d} className={`${mode === d ? 'on' : ''} ${segClass(d)}`} onClick={() => setMode(d)}>
                {decisionVerb(d, lang)}
              </button>
            ))}
        </div>
        {mode === 'accepted' && (
          <p className="hint">
            {order.my_side === 'seller' ? t('you_give') : t('you_save')}{' '}
            <b className="num">
              −{pct(theirs)}% ≈ {amd(Math.round((order.subtotal_amd * theirs) / 10_000))}
            </b>
          </p>
        )}
        {mode === 'declined' && (
          <p className="hint">
            {t('price_stays')} <b className="num">{amd(order.subtotal_amd)}</b>
          </p>
        )}
        {mode === 'countered' && (
          <>
            <div className="dial-presets">
              {[...new Set([half, 200, 300, 400, 500, 600, 700, 800, 1000])]
                .filter((bp) => bp >= lo && bp <= hi)
                .sort((a, b) => a - b)
                .map((bp) => (
                  <button key={bp} className={percent === bp ? 'on' : ''} onClick={() => setPercent(bp)}>
                    −{pct(bp)}%{bp === half ? ` · ${t('halfway').toLowerCase()}` : ''}
                  </button>
                ))}
            </div>
            {nego.rounds_left === 1 && <p className="hint">{t('last_move')}</p>}
          </>
        )}
        <p className="hint muted">{t('no_answer_cancels')}</p>
        <button
          className="btn primary block"
          disabled={busy || (mode === 'countered' && percent === null)}
          onClick={() => onAnswer({ order: { decision: mode, ...(mode === 'countered' ? { percent_bp: percent ?? 0 } : {}) } })}
        >
          {mode === 'accepted' ? `${t('accept')} −${theirs / 100}%` : t('send_answer')}
        </button>
      </div>
    )
  }

  const openLines = open.lines.filter((ol) => names[ol.order_line_id]?.final_price_amd === null)
  return (
    <div className="answer">
      {openLines.map((ol) => {
        const line = names[ol.order_line_id]
        const a = answers[ol.order_line_id] ?? { decision: 'accepted' as Decision, price: null }
        const set = (patch: Partial<typeof a>) => setAnswers((all) => ({ ...all, [ol.order_line_id]: { ...a, ...patch } }))
        const own = lastOwn(ol.order_line_id)
        const [lo, hi] =
          order.my_side === 'seller'
            ? [ol.proposed_price_amd + 10, Math.min(own ?? line.list_price_amd, line.list_price_amd) - 10]
            : [(own ?? Math.ceil(line.list_price_amd * 0.5)) + 10, ol.proposed_price_amd - 10]
        const half =
          order.my_side === 'seller'
            ? midpoint(ol.proposed_price_amd, Math.min(own ?? line.list_price_amd, line.list_price_amd))
            : midpoint(own ?? ol.proposed_price_amd, ol.proposed_price_amd)
        return (
          <div key={ol.order_line_id} className="answer-line">
            <div className="line-name">
              {tr(line.name_snapshot, lang)}: <span className="num">{amd(ol.proposed_price_amd)}</span>
              <span className="muted small"> / {unit(line.unit, lang)}</span>
            </div>
            <div className="seg">
              {(['accepted', 'countered', 'declined'] as Decision[])
                .filter((d) => d !== 'countered' || (canCounter && hi >= lo))
                .map((d) => (
                  <button key={d} className={`${a.decision === d ? 'on' : ''} ${segClass(d)}`} onClick={() => set({ decision: d })}>
                    {decisionVerb(d, lang)}
                  </button>
                ))}
            </div>
            <LineHint
              side={order.my_side}
              decision={a.decision}
              list={line.list_price_amd}
              theirs={ol.proposed_price_amd}
              qty={Number(line.qty_ordered)}
            />
            {a.decision === 'countered' && (
              <div className="hint-row">
                <span className="muted small num">
                  {t('counter_range')} {amd(lo)} – {amd(hi)}
                </span>
                {half >= lo && half <= hi && (
                  <button type="button" className="pill haggle" onClick={() => set({ price: half })}>
                    {t('halfway')}: {amd(half)}
                  </button>
                )}
              </div>
            )}
            {a.decision === 'countered' && (
              <PriceDial
                reference={line.list_price_amd}
                qty={Number(line.qty_ordered)}
                value={a.price}
                min={lo}
                max={hi}
                unitLabel={unit(line.unit, lang)}
                presets={[]}
                autoCommit
                showSaving={order.my_side === 'buyer'}
                onCommit={(p) => set({ price: p })}
              />
            )}
          </div>
        )
      })}
      {nego.rounds_left === 1 && <p className="hint">{t('last_move')}</p>}
      <p className="hint muted">{t('no_answer_cancels')}</p>
      <button
        className="btn primary block"
        disabled={busy || openLines.some((ol) => answers[ol.order_line_id]?.decision === 'countered' && answers[ol.order_line_id]?.price === null)}
        onClick={() =>
          onAnswer({
            lines: openLines.map((ol) => {
              const a = answers[ol.order_line_id] ?? { decision: 'accepted' as Decision, price: null }
              return { order_line_id: ol.order_line_id, decision: a.decision, ...(a.decision === 'countered' ? { price_amd: a.price ?? 0 } : {}) }
            }),
          })
        }
      >
        {t('send_answer')}
      </button>
    </div>
  )
}

/** What the chosen answer means in money for this line. */
function LineHint({
  side,
  decision,
  list,
  theirs,
  qty: q,
}: {
  side: 'buyer' | 'seller'
  decision: Decision
  list: number
  theirs: number
  qty: number
}) {
  const t = useT()
  if (decision === 'accepted')
    return (
      <p className="hint">
        {side === 'seller' ? t('you_give') : t('you_save')}{' '}
        <b className="num">
          {amd(Math.round((list - theirs) * q))} · −{pct(Math.floor(((list - theirs) * 10_000) / list))}%
        </b>
      </p>
    )
  if (decision === 'declined')
    return (
      <p className="hint">
        {t('price_stays')} <b className="num">{amd(list)}</b>
      </p>
    )
  return null
}

function segClass(d: Decision) {
  return d === 'accepted' ? 'accept' : d === 'declined' ? 'decline' : 'counter'
}

function decisionVerb(d: Decision, lang: 'hy' | 'ru' | 'en'): string {
  return { accepted: TEXTS.accept, countered: TEXTS.counter, declined: TEXTS.decline }[d][lang]
}

function responseText(r: NonNullable<Offer['response']>, lang: 'hy' | 'ru' | 'en'): string {
  const map: Record<string, Record<'hy' | 'ru' | 'en', string>> = {
    accepted: { hy: 'ընդունված', ru: 'принято', en: 'accepted' },
    countered: { hy: 'հակաառաջարկ', ru: 'встречная', en: 'countered' },
    declined: { hy: 'մերժված', ru: 'отклонено', en: 'declined' },
    expired: { hy: 'ժամկետանց', ru: 'истекло', en: 'expired' },
    superseded: { hy: 'փոխարինված', ru: 'заменено', en: 'replaced' },
  }
  return map[r][lang]
}
