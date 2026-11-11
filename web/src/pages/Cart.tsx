import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { HaggleHowTo, howToSeen } from '../components/HaggleHowTo'
import { PriceDial } from '../components/PriceDial'
import { QtyStepper } from '../components/QtyStepper'
import { CartSuggestions, SellerSuggestions, TierHint } from '../components/Suggestions'
import { OrderMeter } from '../components/ui'
import { fill, problemText, SOURCES, useLang, useT } from '../i18n'
import { api, ApiError, type Address, type CartGroup, type CartLine, type Product, type Suggestion } from '../lib/api'
import { useCart } from '../lib/cart'
import { amd, date, slot, tr, unit } from '../lib/format'
import { pct, priceForDiscount } from '../lib/haggle'
import { useSession } from '../lib/session'

const DISCOUNTS = [3, 5, 7, 10]

/** The buyer's delivery pick for one seller: a slot start ("10:00:00") or any time, regular or urgent. */
interface Pick {
  slot: string | null
  express: boolean
}
const NO_PICK: Pick = { slot: null, express: false }

/** Urgent-delivery surcharge the buyer picked for this seller (the cart API counts regular delivery only). */
function expressFee(g: CartGroup, p: Pick | undefined): number {
  return p?.express && g.express && !g.express.problem ? g.express.fee_amd : 0
}

/** What the buyer saves if every offer in the cart is accepted. */
function potentialSaving(groups: CartGroup[], discounts: Record<string, number>): number {
  let won = 0
  for (const g of groups) {
    for (const l of g.lines) {
      if (l.offer_price_amd !== null) won += Math.max(0, (l.unit_price_amd - l.offer_price_amd) * Number(l.qty))
    }
    won += Math.round((g.subtotal_amd * (discounts[g.seller.id] ?? 0)) / 100)
  }
  return Math.round(won)
}

export function CartPage() {
  const { guest } = useCart()
  return guest ? <GuestCart /> : <ServerCart />
}

/** Catalog price for a quantity: the deepest volume tier reached, else the list price. */
function listPrice(p: Product, q: number): number {
  let price = p.base_price_amd
  for (const tier of p.tiers) if (q >= Number(tier.min_qty)) price = Math.min(price, tier.price_amd)
  return price
}

/**
 * Cart before the company exists: kept in the browser, priced from the public catalog.
 * Sellers are hidden for guests, so lines are not grouped; ordering asks to sign in or register first.
 */
function GuestCart() {
  const t = useT()
  const lang = useLang()
  const { me } = useSession()
  const { local, setItem, remove } = useCart()
  const [products, setProducts] = useState<Product[] | null>(null)
  const ids = Object.keys(local)
  const key = ids.sort().join(',')

  useEffect(() => {
    if (!key) return setProducts([])
    api.products({ ids: key.split(','), limit: 100 }).then((r) => setProducts(r.items), () => setProducts([]))
  }, [key])

  if (!products) return <p className="muted">{t('loading')}</p>
  const lines = products.filter((p) => local[p.id])
  if (lines.length === 0) {
    return (
      <div className="empty page">
        <div className="big">🧾</div>
        <p>{t('cart_empty')}</p>
        <Link className="btn primary" to="/">
          {t('tab_catalog')}
        </Link>
      </div>
    )
  }
  const total = lines.reduce((s, p) => s + Math.round(listPrice(p, Number(local[p.id])) * Number(local[p.id])), 0)

  return (
    <div className="stack page">
      <h1 className="page-title">{t('tab_cart')}</h1>
      <section className="ticket">
        <ul className="lines">
          {lines.map((p) => {
            const q = local[p.id]
            const price = listPrice(p, Number(q))
            return (
              <li key={p.id} className="line">
                <div className="line-main">
                  <div>
                    <div className="line-name">{tr(p.name, lang)}</div>
                    <div className="line-sub num">
                      {price !== p.base_price_amd && <span className="strike">{amd(p.base_price_amd)}</span>} {amd(price)} /{' '}
                      {unit(p.unit, lang)}
                    </div>
                  </div>
                  <div className="line-total num">{amd(Math.round(price * Number(q)))}</div>
                </div>
                <div className="line-tools">
                  <QtyStepper
                    value={q}
                    min={p.min_qty}
                    step={p.qty_step}
                    unitLabel={unit(p.unit, lang)}
                    onChange={(next) => void (Number(next) <= 0 ? remove(p.id) : setItem(p.id, next))}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      </section>
      <section className="ticket checkout">
        <div className="total-row">
          <span>{t('total')}</span>
          <strong className="num">≈ {amd(total)}</strong>
        </div>
        <p className="muted small" style={{ margin: 0 }}>
          {t('guest_hint')}
        </p>
        <Link className="btn primary block" to={me ? '/onboarding' : '/login'}>
          {me ? t('register_to_order') : t('sign_in_to_order')} →
        </Link>
      </section>
    </div>
  )
}

function ServerCart() {
  const t = useT()
  const lang = useLang()
  const navigate = useNavigate()
  const { cart, refresh } = useCart()
  const [addresses, setAddresses] = useState<Address[]>([])
  const [addrReady, setAddrReady] = useState(false)
  const [addressId, setAddressId] = useState<string | undefined>()
  const [discounts, setDiscounts] = useState<Record<string, number>>({})
  const [picks, setPicks] = useState<Record<string, Pick>>({})
  const [sending, setSending] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  // One key per attempt: a double tap or a retry never creates the orders twice
  const attempt = useRef(crypto.randomUUID())

  useEffect(() => {
    void refresh().catch(() => {})
    api.addresses().then((list) => {
      setAddresses(list)
      setAddressId((id) => id ?? list[0]?.id)
      setAddrReady(true)
    })
  }, [refresh])

  // Suggestions follow the cart: each change may close a gap or open a volume price
  useEffect(() => {
    if (!cart?.groups.length) return setSuggestions([])
    api.cartSuggestions(addressId).then(setSuggestions, () => setSuggestions([]))
  }, [cart, addressId])

  if (!cart) return <p className="muted">{t('loading')}</p>
  if (cart.groups.length === 0) {
    return (
      <div className="empty page">
        <div className="big">🧾</div>
        <p>{t('cart_empty')}</p>
        <Link className="btn primary" to="/">
          {t('tab_catalog')}
        </Link>
      </div>
    )
  }

  // An order discount only counts for sellers without line offers (the API accepts one kind per order)
  const effective = Object.fromEntries(
    cart.groups
      .filter((g) => !g.lines.some((l) => l.offer_price_amd !== null))
      .map((g) => [g.seller.id, discounts[g.seller.id] ?? 0]),
  )
  const saving = potentialSaving(cart.groups, effective)
  const total = cart.total_amd + cart.groups.reduce((sum, g) => sum + expressFee(g, picks[g.seller.id]), 0)

  // Why the button is off, said next to it: the reasons themselves sit screens above, in each supplier's card
  const blockers: { key: string; text: string; href?: string }[] = cart.problems.map((p, i) => {
    const g = cart.groups.find((x) => x.seller.id === p.seller_id || x.lines.some((l) => l.product_id === p.product_id))
    const name = g?.seller.display_name ?? ''
    const text =
      p.code === 'min_order_not_reached' && g
        ? fill(t('cart_blocked_min'), { name, sum: amd(g.missing_amd) })
        : [name, problemText(p.code, lang)].filter(Boolean).join(': ')
    return { key: `${p.code}-${i}`, text, href: g ? `#seller-${g.seller.id}` : undefined }
  })
  if (addrReady && addresses.length === 0) blockers.push({ key: 'address', text: t('add_delivery_address') })

  async function send() {
    setSending(true)
    setErrors([])
    try {
      const order_offers = Object.entries(effective)
        .filter(([, v]) => v > 0)
        .map(([seller_id, v]) => ({ seller_id, percent_bp: Math.round(v * 100) }))
      const deliveries = cart!.groups.map((g) => {
        const p = picks[g.seller.id] ?? NO_PICK
        const express = expressFee(g, p) > 0
        return { seller_id: g.seller.id, slot_start: express ? null : p.slot, express }
      })
      const { orders } = await api.checkout({ address_id: addressId, order_offers, deliveries }, attempt.current)
      attempt.current = crypto.randomUUID()
      navigator.vibrate?.([10, 40, 10])
      await refresh()
      navigate(orders.length === 1 ? `/orders/${orders[0].id}` : '/orders', { state: { sent: true } })
    } catch (e) {
      const err = e as ApiError
      setErrors(err.problems?.length ? err.problems.map((p) => problemText(p.code, lang)) : [problemText(err.code, lang)])
      attempt.current = crypto.randomUUID()
      await refresh().catch(() => {})
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="stack page">
      <h1 className="page-title">{t('tab_cart')}</h1>
      {/* The address comes first: delivery days, fees and suggestions depend on it */}
      <section className="ticket cart-address">
        {addresses.length > 0 && (
          <label className="field">
            <span>{t('address')}</span>
            <select value={addressId} onChange={(e) => setAddressId(e.target.value)}>
              {addresses.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}: {a.text}
                </option>
              ))}
            </select>
          </label>
        )}
        {(() => {
          const a = addresses.find((x) => x.id === addressId)
          return a && (a.contact_name || a.contact_phone) ? (
            <p className="muted small" style={{ margin: 0 }}>
              {t('receiver')}: {[a.contact_name, a.contact_phone].filter(Boolean).join(', ')}
            </p>
          ) : null
        })()}
        {addresses.length === 0 && (
          <Link to="/company#addresses" className="btn accent block">
            + {t('add_delivery_address')}
          </Link>
        )}
      </section>
      {cart.groups.map((g) => (
        <SellerTicket
          key={g.seller.id}
          g={g}
          canNegotiate={cart.can_negotiate}
          rounds={cart.offer_max_rounds}
          discount={discounts[g.seller.id] ?? 0}
          onDiscount={(v) => setDiscounts((d) => ({ ...d, [g.seller.id]: v }))}
          pick={picks[g.seller.id] ?? NO_PICK}
          onPick={(p) => setPicks((x) => ({ ...x, [g.seller.id]: p }))}
          suggestions={suggestions.filter((x) => x.seller.id === g.seller.id)}
        />
      ))}
      <CartSuggestions items={suggestions} />

      <section className="ticket checkout">
        <div className="total-row">
          <span>
            {t('total')}
            <span className="muted small">
              {' '}
              · {cart.groups.length} {t('suppliers')}
            </span>
          </span>
          <strong className="num">{amd(total)}</strong>
        </div>
        {saving > 0 && (
          <div className="total-row small">
            <span className="muted">{t('if_accepted')}</span>
            <span className="pill saving num">−{amd(saving)}</span>
          </div>
        )}
        {errors.map((e) => (
          <p key={e} className="error">
            {e}
          </p>
        ))}
        {blockers.length > 0 && (
          <div className="blockers" role="status">
            <b>{t('cart_blocked')}</b>
            <ul>
              {blockers.map((b) => (
                <li key={b.key}>{b.href ? <a href={b.href}>{b.text}</a> : b.text}</li>
              ))}
            </ul>
          </div>
        )}
        <button className="btn primary block" disabled={sending || !addrReady || blockers.length > 0} onClick={send}>
          {t('send_all')} <span className="sub num">· {amd(total)}</span>
        </button>
      </section>
    </div>
  )
}

function SellerTicket({
  g,
  canNegotiate,
  rounds,
  discount,
  onDiscount,
  suggestions,
  pick,
  onPick,
}: {
  pick: Pick
  onPick: (p: Pick) => void
  g: CartGroup
  canNegotiate: boolean
  rounds: number
  suggestions: Suggestion[]
  discount: number
  onDiscount: (v: number) => void
}) {
  const t = useT()
  const lang = useLang()
  const hasLineOffers = g.lines.some((l) => l.offer_price_amd !== null)
  const urgent = expressFee(g, pick)
  return (
    <section className="ticket perf" id={`seller-${g.seller.id}`}>
      <header className="ticket-head">
        <h2>{g.seller.display_name}</h2>
        {urgent > 0 ? (
          <span className="pill express">⚡ {fill(t('express_in'), { h: g.express!.hours })}</span>
        ) : (
          g.delivery_date && (
            <span className="pill plain">
              🚚 {date(g.delivery_date, lang)}
              {pick.slot && `, ${slot(pick.slot, g.slots.find((x) => x.start === pick.slot)?.end ?? pick.slot)}`}
            </span>
          )
        )}
      </header>
      <OrderMeter subtotal={g.subtotal_amd} min={g.min_order_amd} freeFrom={g.free_delivery_from_amd} />
      {g.missing_amd > 0 && (
        <p className={g.small_order_fee_amd !== null ? 'min-note' : 'min-note stop'}>
          {g.small_order_fee_amd !== null
            ? fill(t('below_min_or_pay'), { fee: amd(g.small_order_fee_amd) })
            : t('below_min_must')}
        </p>
      )}
      <ul className="lines">
        {g.lines.map((l) => (
          <Line
            key={l.product_id}
            l={l}
            canNegotiate={canNegotiate}
            hours={g.offer_response_hours}
            rounds={rounds}
            tier={suggestions.find((x) => x.kind === 'tier' && x.product_id === l.product_id)}
          />
        ))}
      </ul>
      <SellerSuggestions items={suggestions} />
      <hr className="rule" />
      <dl className="sums">
        <dt>{t('subtotal')}</dt>
        <dd>{amd(g.subtotal_amd)}</dd>
        <dt>{t('delivery')}</dt>
        <dd>{g.delivery_fee_amd ? amd(g.delivery_fee_amd) : t('free')}</dd>
        {urgent > 0 && (
          <>
            <dt>{t('express_fee_row')}</dt>
            <dd>{amd(urgent)}</dd>
          </>
        )}
      </dl>
      <DeliveryPick g={g} pick={pick} onPick={onPick} />
      {g.problems
        .filter((p) => p !== 'min_order_not_reached')
        .map((p) => (
          <p key={p} className="error small">
            {problemText(p, lang)}
          </p>
        ))}
      {canNegotiate && !hasLineOffers && (
        <div className="field" style={{ marginTop: 12 }}>
          <span>⇄ {t('discount_offer')}</span>
          <div className="dial-presets">
            {[0, ...DISCOUNTS].map((d) => (
              <button key={d} type="button" className={discount === d ? 'on' : ''} onClick={() => onDiscount(d)}>
                {d === 0 ? '—' : `−${d}%`}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

/** When to deliver: regular (any time or a seller's slot) or urgent today, if the seller offers it. */
function DeliveryPick({ g, pick, onPick }: { g: CartGroup; pick: Pick; onPick: (p: Pick) => void }) {
  const t = useT()
  const lang = useLang()
  const ex = g.express
  if (!ex && g.slots.length === 0) return null
  const urgent = pick.express && !!ex && !ex.problem
  return (
    <div className="delivery-pick">
      <span className="eyebrow">{t('delivery_when')}</span>
      {ex && (
        <div className="mode-switch" role="group" aria-label={t('delivery_when')}>
          <button type="button" className={urgent ? '' : 'on'} aria-pressed={!urgent} onClick={() => onPick({ ...pick, express: false })}>
            {t('delivery_regular')}
          </button>
          <button
            type="button"
            className={urgent ? 'on' : ''}
            aria-pressed={urgent}
            disabled={!!ex.problem}
            onClick={() => onPick({ ...pick, express: true })}
          >
            ⚡ {t('delivery_express')} <small className="num">+{amd(ex.fee_amd)}</small>
          </button>
        </div>
      )}
      {ex?.problem && <p className="muted small">⚡ {problemText(ex.problem, lang)}</p>}
      {urgent && <p className="muted small">⚡ {fill(t('express_in'), { h: ex!.hours })}</p>}
      {!urgent && g.slots.length > 0 && (
        <div className="slot-chips" role="group" aria-label={t('slots_title')}>
          <button type="button" className={pick.slot === null ? 'chip on' : 'chip'} onClick={() => onPick({ ...pick, slot: null })}>
            {t('slot_any')}
          </button>
          {g.slots.map((x) => (
            <button
              type="button"
              key={x.start}
              className={pick.slot === x.start ? 'chip on num' : 'chip num'}
              aria-pressed={pick.slot === x.start}
              onClick={() => onPick({ ...pick, slot: x.start })}
            >
              {slot(x.start, x.end)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Line({
  l,
  canNegotiate,
  hours,
  rounds,
  tier,
}: {
  l: CartLine
  canNegotiate: boolean
  hours: number
  rounds: number
  tier?: Suggestion
}) {
  const t = useT()
  const lang = useLang()
  const { setItem, remove } = useCart()
  const [busy, setBusy] = useState(false)
  const [offerOpen, setOfferOpen] = useState(false)
  const [howTo, setHowTo] = useState(false)

  async function run(p: Promise<void>) {
    setBusy(true)
    try {
      await p
    } catch {
      /* the cart shows the problem */
    } finally {
      setBusy(false)
    }
  }

  const changeQty = (next: string) =>
    run(Number(next) <= 0 ? remove(l.product_id) : setItem(l.product_id, next, l.offer_price_amd))
  const canOffer = canNegotiate && l.offers_allowed
  const tooSmall = l.line_total_amd < l.offer_min_line_amd
  const offered = l.offer_price_amd

  return (
    <li className="line">
      <div className="line-main">
        <div>
          <div className="line-name">{tr(l.name, lang)}</div>
          <div className="line-sub num">
            {l.unit_price_amd !== l.base_price_amd && <span className="strike">{amd(l.base_price_amd)}</span>}{' '}
            {amd(l.unit_price_amd)} / {unit(l.unit, lang)}
            {l.price_source !== 'list' && <span style={{ color: 'var(--herb)' }}> · {SOURCES[l.price_source][lang]}</span>}
          </div>
        </div>
        <div className="line-total num">
          {offered !== null ? (
            <>
              <div className="strike small">{amd(l.line_total_amd)}</div>
              <div style={{ color: 'var(--brand)' }}>{amd(Math.round(offered * Number(l.qty)))}</div>
            </>
          ) : (
            amd(l.line_total_amd)
          )}
        </div>
      </div>
      <div className="line-tools">
        <QtyStepper value={l.qty} min={l.min_qty} step={l.qty_step} unitLabel={unit(l.unit, lang)} onChange={changeQty} disabled={busy} />
        {canOffer &&
          (offered !== null ? (
            <button className="pill brand" style={{ border: 0 }} onClick={() => setOfferOpen((o) => !o)}>
              ⇄ {amd(offered)} · −{Math.round((1 - offered / l.unit_price_amd) * 100)}%
            </button>
          ) : (
            <button
              className="link small"
              disabled={tooSmall}
              onClick={() => {
                setOfferOpen((o) => !o)
                if (!howToSeen()) setHowTo(true)
              }}
            >
              ⇄ {t('offer_price')}
            </button>
          ))}
      </div>
      {tier && offered === null && <TierHint s={tier} currentQty={l.qty} />}
      {canOffer && tooSmall && offered === null && (
        <p className="muted small" style={{ margin: '6px 0 0' }}>
          {t('offer_hint')} {amd(l.offer_min_line_amd)}
        </p>
      )}
      {canOffer && offerOpen && !tooSmall && (
        <>
          {howTo && <HaggleHowTo hours={hours} rounds={rounds} onClose={() => setHowTo(false)} />}
          <div className="hint-row">
            <span className={l.offer_hint_bp ? 'hint strong' : 'hint muted'}>
              {l.offer_hint_bp ? fill(t('hint_seller_agrees'), { p: pct(l.offer_hint_bp) }) : t('hint_generic')}
            </span>
            {!howTo && (
              <button type="button" className="link small" onClick={() => setHowTo(true)}>
                {t('howto_link')}
              </button>
            )}
          </div>
          <PriceDial
            likelyFrom={l.offer_hint_bp ? priceForDiscount(l.unit_price_amd, l.offer_hint_bp) : null}
            reference={l.unit_price_amd}
            qty={Number(l.qty)}
            value={offered}
            min={Math.ceil(l.unit_price_amd * 0.5)}
            max={l.unit_price_amd - 10}
            unitLabel={unit(l.unit, lang)}
            onCommit={(p) => void run(setItem(l.product_id, l.qty, p))}
          />
          {offered !== null && (
            <button
              className="link small"
              onClick={() => {
                setOfferOpen(false)
                void run(setItem(l.product_id, l.qty, null))
              }}
            >
              {t('remove_offer')}
            </button>
          )}
        </>
      )}
      {l.problems.map((p) => (
        <p key={p} className="error small">
          {problemText(p, lang)}
        </p>
      ))}
    </li>
  )
}
