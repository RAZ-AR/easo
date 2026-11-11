import type React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ConsentGate } from './components/Consent'
import { ActiveOrders } from './components/OrderTracker'
import { Sheet } from './components/Sheet'
import { Icon } from './components/ui'
import { LangContext, useT, type TextKey } from './i18n'
import type { Lang, Mode } from './lib/api'
import { CartProvider, useCart } from './lib/cart'
import { amd } from './lib/format'
import { NoticesProvider, useNotices } from './lib/notices'
import { pendingInvite } from './lib/invite'
import { SessionProvider, useSession } from './lib/session'
import { CartPage } from './pages/Cart'
import { Catalog } from './pages/Catalog'
import { Company } from './pages/Company'
import { Login } from './pages/Login'
import { Moderation } from './pages/Moderation'
import { Onboarding } from './pages/Onboarding'
import { OrderDetail } from './pages/OrderDetail'
import { Orders } from './pages/Orders'
import { Documents } from './pages/Documents'
import { Notifications } from './pages/Notifications'
import { Legal } from './pages/Legal'
import { PrintBatch, PrintDocument } from './pages/PrintDocument'
import { PriceImport } from './pages/PriceImport'
import { ProductCard } from './pages/ProductCard'
import { SellerProducts } from './pages/SellerProducts'
import { SellerSettings } from './pages/SellerSettings'
import { Supplier } from './pages/Supplier'
import { Clients } from './pages/Clients'

const LANG_KEY = 'easo.lang'
const LANGS: Lang[] = ['hy', 'ru', 'en']

function savedLang(): Lang {
  try {
    const v = localStorage.getItem(LANG_KEY)
    return LANGS.includes(v as Lang) ? (v as Lang) : 'hy'
  } catch {
    return 'hy'
  }
}

export function App() {
  const [lang, setLangState] = useState<Lang>(savedLang)
  const setLang = useCallback((l: Lang) => {
    setLangState(l)
    try {
      localStorage.setItem(LANG_KEY, l)
    } catch {
      /* private mode: language just isn't remembered */
    }
  }, [])
  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])
  const ctx = useMemo(() => ({ lang, setLang }), [lang, setLang])

  return (
    <LangContext.Provider value={ctx}>
      <SessionProvider>
        <Routes>
          {/* A4 printout: no app chrome */}
          <Route path="/print/batch" element={<PrintBatch />} />
          <Route path="/print/:id" element={<PrintDocument />} />
          <Route path="/legal/:doc" element={<Legal />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </SessionProvider>
    </LangContext.Provider>
  )
}

type State = 'guest' | 'no_company' | 'admin' | 'buyer' | 'seller'

interface NavItem {
  to: string
  label: TextKey
  icon: () => React.ReactNode
  badge?: number
  inMore?: boolean // on the phone it sits in the dock's "More" sheet (the dock holds five slots at most)
}

function useAppState(): State {
  const { me, org, mode } = useSession()
  if (!me) return 'guest'
  if (!org) return me.is_platform_admin ? 'admin' : 'no_company'
  return mode
}

function Shell() {
  const state = useAppState()
  const server = state === 'buyer'
  return (
    <NoticesProvider enabled={state !== 'guest' && state !== 'admin'}>
      <CartProvider server={server}>
        <Layout state={state} />
      </CartProvider>
    </NoticesProvider>
  )
}

function Layout({ state }: { state: State }) {
  const t = useT()
  const { pathname } = useLocation()
  const { count } = useCart()
  const nav = navFor(state, count)
  // The tab title names the page, so history and open tabs can be told apart
  const here = nav.find((n) => (n.to === '/' ? pathname === '/' : pathname === n.to || pathname.startsWith(n.to + '/')))
  const pageTitle = here ? t(here.label) : null
  useEffect(() => {
    document.title = pageTitle ? `${pageTitle} — Easo` : 'Easo'
  }, [pageTitle])
  const showCartBar = (state === 'buyer' || state === 'guest' || state === 'no_company') && pathname === '/' && count > 0

  return (
    <div className={`layout${showCartBar ? ' has-cartbar' : ''}`}>
      <Sidebar nav={nav} state={state} />
      <div className="content">
        <MobileTopBar state={state} />
        <main>
          <Banner state={state} />
          {state === 'buyer' && <ActiveOrders />}
          <Routes>{routesFor(state)}</Routes>
        </main>
      </div>
      <Dock nav={nav} state={state} />
      {showCartBar && <CartBar />}
      <ConsentGate />
    </div>
  )
}

function navFor(state: State, cartCount: number): NavItem[] {
  switch (state) {
    case 'guest':
      return [
        { to: '/', label: 'tab_catalog', icon: Icon.catalog },
        { to: '/cart', label: 'tab_cart', icon: Icon.cart, badge: cartCount },
        { to: '/login', label: 'sign_in', icon: Icon.user },
      ]
    case 'no_company':
      return [
        { to: '/', label: 'tab_catalog', icon: Icon.catalog },
        { to: '/cart', label: 'tab_cart', icon: Icon.cart, badge: cartCount },
        { to: '/onboarding', label: 'tab_company', icon: Icon.company },
      ]
    case 'admin':
      return [{ to: '/', label: 'tab_moderation', icon: Icon.inbox }]
    case 'buyer':
      return [
        { to: '/', label: 'tab_catalog', icon: Icon.catalog },
        { to: '/cart', label: 'tab_cart', icon: Icon.cart, badge: cartCount },
        { to: '/orders', label: 'tab_orders', icon: Icon.ticket },
        { to: '/documents', label: 'tab_documents', icon: Icon.doc, inMore: true },
        { to: '/company', label: 'tab_company', icon: Icon.company },
      ]
    case 'seller':
      return [
        { to: '/', label: 'tab_inbox', icon: Icon.inbox },
        { to: '/orders', label: 'tab_orders', icon: Icon.ticket },
        { to: '/seller/products', label: 'tab_products', icon: Icon.catalog },
        { to: '/seller/settings', label: 'tab_delivery', icon: Icon.truck },
        { to: '/seller/clients', label: 'tab_clients', icon: Icon.people, inMore: true },
        { to: '/documents', label: 'tab_documents', icon: Icon.doc, inMore: true },
        { to: '/company', label: 'tab_company', icon: Icon.company, inMore: true },
      ]
  }
}

/** Signed in on /login: back to the supplier link the visitor came through, else home. */
function AfterSignIn() {
  const invite = pendingInvite.get()
  return <Navigate to={invite ? `/s/${invite}` : '/'} replace />
}

/** Company just registered: back to the supplier it came for (now its client), else the documents to upload. */
function AfterSignUp() {
  const invite = pendingInvite.get()
  return <Navigate to={invite ? `/s/${invite}` : '/company'} replace />
}

function routesFor(state: State) {
  switch (state) {
    case 'guest':
      return (
        <>
          <Route path="/" element={<Catalog canBuy />} />
          <Route path="/product/:id" element={<ProductCard canBuy />} />
          <Route path="/s/:code" element={<Supplier />} />
          <Route path="/cart" element={<CartPage />} />
          <Route path="/login" element={<Login />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </>
      )
    case 'no_company':
      return (
        <>
          <Route path="/" element={<Catalog canBuy />} />
          <Route path="/product/:id" element={<ProductCard canBuy />} />
          <Route path="/s/:code" element={<Supplier />} />
          <Route path="/cart" element={<CartPage />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/login" element={<AfterSignIn />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </>
      )
    case 'admin':
      return (
        <>
          <Route path="/" element={<Moderation />} />
          <Route path="/login" element={<AfterSignIn />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </>
      )
    case 'buyer':
      return (
        <>
          <Route path="/" element={<Catalog canBuy />} />
          <Route path="/product/:id" element={<ProductCard canBuy />} />
          <Route path="/s/:code" element={<Supplier />} />
          <Route path="/cart" element={<CartPage />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/orders/:id" element={<OrderDetail />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/company" element={<Company />} />
          {/* Just registered: go on to the documents */}
          <Route path="/onboarding" element={<AfterSignUp />} />
          <Route path="/login" element={<AfterSignIn />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </>
      )
    case 'seller':
      return (
        <>
          <Route path="/" element={<Orders inbox />} />
          <Route path="/product/:id" element={<ProductCard canBuy={false} />} />
          <Route path="/s/:code" element={<Supplier />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/orders/:id" element={<OrderDetail />} />
          <Route path="/seller/products" element={<SellerProducts />} />
          <Route path="/seller/import" element={<PriceImport />} />
          <Route path="/seller/settings" element={<SellerSettings />} />
          <Route path="/seller/clients" element={<Clients />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/company" element={<Company />} />
          {/* Just registered: go on to the documents */}
          <Route path="/onboarding" element={<AfterSignUp />} />
          <Route path="/login" element={<AfterSignIn />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </>
      )
  }
}

/** What blocks the current side, said once at the top of every page. */
function Banner({ state }: { state: State }) {
  const t = useT()
  const { org, buyerReady, canSell } = useSession()
  const { pathname } = useLocation()
  if (pathname === '/company' || pathname === '/onboarding') return null
  let text: TextKey | null = null
  let to = '/company'
  if (state === 'no_company') {
    text = 'register_company_cta'
    to = '/onboarding'
  } else if (state === 'buyer' && !buyerReady) text = 'upload_to_buy'
  else if (state === 'seller' && !canSell)
    text = org?.verification_status === 'pending' ? 'pending_seller_banner' : 'upload_to_sell'
  if (!text) return null
  return (
    <Link to={to} className="next banner">
      <strong>{t(text)}</strong>
      <span className="clock">→</span>
    </Link>
  )
}

function ModeSwitch({ compact = false }: { compact?: boolean }) {
  const t = useT()
  const navigate = useNavigate()
  const { org, mode, setMode } = useSession()
  if (!org || !(org.is_buyer && org.is_seller)) return null
  const pick = (m: Mode) => {
    setMode(m)
    navigate('/')
  }
  return (
    <div className={compact ? 'mode-switch compact' : 'mode-switch'} role="group" aria-label={t('mode')}>
      <button className={mode === 'buyer' ? 'on' : ''} onClick={() => pick('buyer')} aria-pressed={mode === 'buyer'}>
        {t('mode_buy')}
      </button>
      <button className={mode === 'seller' ? 'on' : ''} onClick={() => pick('seller')} aria-pressed={mode === 'seller'}>
        {t('mode_sell')}
      </button>
    </div>
  )
}

function Who() {
  const t = useT()
  const { me, org } = useSession()
  if (!me) return <span className="muted small">{t('guest')}</span>
  return (
    <div className="who">
      <span>{org?.display_name ?? (me.is_platform_admin ? t('tab_moderation') : me.name)}</span>
      <small>{me.name}</small>
    </div>
  )
}

function SignOutButton() {
  const t = useT()
  const navigate = useNavigate()
  const { me, signOut } = useSession()
  if (!me) {
    return (
      <Link className="btn primary small-btn" to="/login">
        {t('sign_in')}
      </Link>
    )
  }
  return (
    <button
      className="link"
      onClick={() => {
        signOut()
        navigate('/')
      }}
    >
      {t('logout')}
    </button>
  )
}

function Sidebar({ nav, state }: { nav: NavItem[]; state: State }) {
  const t = useT()
  return (
    <aside className="sidebar">
      <div className="side-top">
        <Link to="/" className="side-brand">
          <span className="brand">Easo</span>
          <span>
            <b>Easo</b>
            <small>HoReCa</small>
          </span>
        </Link>
        {state !== 'guest' && state !== 'admin' && <Bell />}
      </div>
      <ModeSwitch />
      <nav className="side-nav">
        {nav.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'}>
            {n.icon()}
            <span>{t(n.label)}</span>
            {n.badge ? <span className="dot">{n.badge}</span> : null}
          </NavLink>
        ))}
      </nav>
      <div className="side-foot">
        <Who />
        <div className="side-row">
          <LangSwitch />
          <SignOutButton />
        </div>
        {state === 'guest' && <p className="muted small">{t('guest_hint')}</p>}
        <p className="legal-links small">
          <Link to="/legal/terms">{t('legal_terms')}</Link> · <Link to="/legal/privacy">{t('legal_privacy')}</Link>
        </p>
      </div>
    </aside>
  )
}

function MobileTopBar({ state }: { state: State }) {
  const { org } = useSession()
  const dual = !!org && org.is_buyer && org.is_seller
  return (
    <header className="topbar">
      <Link to="/" className="brand" aria-label="Easo">
        Easo
      </Link>
      {dual ? <ModeSwitch compact /> : state === 'guest' ? <span className="who" /> : <Who />}
      <span className="spacer" />
      {state !== 'guest' && state !== 'admin' ? <Bell /> : <LangCycle />}
    </header>
  )
}

/** Phone header: one round button that steps through hy → ru → en. */
function LangCycle() {
  return (
    <LangContext.Consumer>
      {({ lang, setLang }) => (
        <button
          className="ibtn lang-cycle"
          aria-label="Language"
          onClick={() => setLang(LANGS[(LANGS.indexOf(lang) + 1) % LANGS.length])}
        >
          {lang === 'hy' ? 'Հայ' : lang === 'ru' ? 'Рус' : 'Eng'}
        </button>
      )}
    </LangContext.Consumer>
  )
}

/** Round bell with the unread count; opens the notifications page. */
function Bell() {
  const t = useT()
  const { unread } = useNotices()
  return (
    <NavLink to="/notifications" className="ibtn bell" aria-label={t('tab_notifications')} title={t('tab_notifications')}>
      {Icon.bell()}
      {unread > 0 && <span className="dot">{unread > 99 ? '99+' : unread}</span>}
    </NavLink>
  )
}

function CartBar() {
  const t = useT()
  const { cart, count, guest } = useCart()
  const { me } = useSession()
  return (
    <Link to="/cart" className="cartbar" aria-label={`${t('tab_cart')}: ${count} ${t('items')}`}>
      <div className="info">
        {cart ? (
          <span key={cart.total_amd} className="sum num bump">
            {amd(cart.total_amd)}
          </span>
        ) : (
          <span className="sum">{t('tab_cart')}</span>
        )}
        <small>
          {count} {t('items')}
          {cart && ` · ${cart.groups.length} ${t('suppliers')}`}
          {guest && ` · ${t(me ? 'company_cart_hint' : 'guest_cart_hint')}`}
        </small>
      </div>
      <span className="go">{t('go_cart')} →</span>
    </Link>
  )
}

function Tab({ to, icon, label, badge }: { to: string; icon: () => React.ReactNode; label: string; badge?: number }) {
  return (
    <NavLink to={to} end={to === '/'} aria-label={label} title={label}>
      <span className="tab-icon">
        {icon()}
        {badge ? <span className="dot">{badge > 99 ? '99+' : badge}</span> : null}
      </span>
      <span className="tab-label">{label}</span>
    </NavLink>
  )
}

/** Phone dock: the main sections, plus "More" for the rest of the sidebar (extra pages, language, account). */
function Dock({ nav, state }: { nav: NavItem[]; state: State }) {
  const t = useT()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  useEffect(close, [pathname, close])
  const main = nav.filter((n) => !n.inMore)
  const extra = nav.filter((n) => n.inMore)
  const extraActive = extra.some((n) => pathname === n.to || pathname.startsWith(n.to + '/'))
  return (
    <>
      <nav className="tabbar dock" aria-label="menu" style={{ '--slots': main.length + 1 } as React.CSSProperties}>
        {main.map((n) => (
          <Tab key={n.to} {...n} label={t(n.label)} />
        ))}
        <button
          type="button"
          className={open || extraActive ? 'active' : ''}
          aria-label={t('tab_more')}
          aria-expanded={open}
          title={t('tab_more')}
          onClick={() => setOpen(true)}
        >
          <span className="tab-icon">{Icon.more()}</span>
          <span className="tab-label">{t('tab_more')}</span>
        </button>
      </nav>
      {open && <MoreSheet extra={extra} state={state} onClose={close} />}
    </>
  )
}

function MoreSheet({ extra, state, onClose }: { extra: NavItem[]; state: State; onClose: () => void }) {
  const t = useT()
  const { me } = useSession()
  return (
    <Sheet onClose={onClose}>
      <div className="more-head">
        <Who />
        <button type="button" className="ibtn" aria-label={t('close')} onClick={onClose}>
          ×
        </button>
      </div>
      <ModeSwitch />
      {(extra.length > 0 || (me && state !== 'admin')) && (
        <nav className="more-nav">
          {extra.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}>
              {n.icon()}
              <span>{t(n.label)}</span>
              {n.badge ? <span className="dot">{n.badge}</span> : null}
            </NavLink>
          ))}
          {me && state !== 'admin' && (
            <NavLink to="/notifications">
              {Icon.bell()}
              <span>{t('tab_notifications')}</span>
            </NavLink>
          )}
        </nav>
      )}
      <div className="more-row">
        <span className="muted small">{t('menu_language')}</span>
        <LangSwitch />
      </div>
      {state === 'guest' && <p className="muted small">{t('guest_hint')}</p>}
      <div className="more-row">
        <p className="legal-links small">
          <Link to="/legal/terms">{t('legal_terms')}</Link> · <Link to="/legal/privacy">{t('legal_privacy')}</Link>
        </p>
        <SignOutButton />
      </div>
    </Sheet>
  )
}

export function LangSwitch() {
  return (
    <LangContext.Consumer>
      {({ lang, setLang }) => (
        <div className="lang" role="group" aria-label="Language">
          {LANGS.map((l) => (
            <button key={l} className={l === lang ? 'on' : ''} onClick={() => setLang(l)} aria-pressed={l === lang}>
              {l === 'hy' ? 'Հայ' : l === 'ru' ? 'Рус' : 'Eng'}
            </button>
          ))}
        </div>
      )}
    </LangContext.Consumer>
  )
}
