import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FILE_KINDS, FILE_STATUS, problemText, useLang, useT, VERIFICATION } from '../i18n'
import { api, ApiError, openFile, type DemoUser, type Verification, type VerificationStatus } from '../lib/api'
import { dateTime } from '../lib/format'
import { useSession } from '../lib/session'

const TABS: VerificationStatus[] = ['pending', 'rejected', 'verified']

/** Operator queue: check each company's documents against the registry, then let it trade. */
export function Moderation() {
  const t = useT()
  const lang = useLang()
  const [tab, setTab] = useState<VerificationStatus | 'demo'>('pending')
  const [items, setItems] = useState<Verification[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async () =>
      tab === 'demo'
        ? undefined
        : api.adminQueue([tab]).then(setItems, (e: ApiError) => setError(problemText(e.code, lang))),
    [tab, lang],
  )
  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="stack page" style={{ maxWidth: 900 }}>
      <h1 className="page-title">{t('tab_moderation')}</h1>
      <div className="chips">
        {TABS.map((s) => (
          <button key={s} className={s === tab ? 'chip on' : 'chip'} onClick={() => setTab(s)}>
            {VERIFICATION[s][lang]}
          </button>
        ))}
        <button className={tab === 'demo' ? 'chip on' : 'chip'} onClick={() => setTab('demo')}>
          ▶ {t('demo_tab')}
        </button>
      </div>
      {tab === 'demo' ? (
        <DemoUsers />
      ) : (
        <>
          {error && <p className="error">{error}</p>}
          {items?.length === 0 && <p className="col-empty">{t('queue_empty')}</p>}
          {items?.map((v) => <CompanyCard key={v.organization.id} v={v} reload={load} />)}
        </>
      )}
    </div>
  )
}

function CompanyCard({ v, reload }: { v: Verification; reload: () => Promise<void> }) {
  const t = useT()
  const lang = useLang()
  const o = v.organization
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function run(p: Promise<unknown>) {
    setError(null)
    try {
      await p
      await reload()
    } catch (e) {
      const ex = e as ApiError
      setError(ex.problems?.length ? ex.problems.map((x) => problemText(x.code, lang)).join(' · ') : problemText(ex.code, lang))
    }
  }

  const rows: [string, string | null][] = [
    [t('tin'), o.tin],
    [t('legal_name'), o.legal_name],
    [t('legal_address'), o.legal_address],
    [t('director'), o.director_name],
    [t('reg_number'), o.registration_number],
    [t('tax_regime'), o.tax_regime ? t(`regime_${o.tax_regime}` as 'regime_vat') : null],
    [t('bank_account'), o.bank_account ? `${o.bank_name ?? ''} ${o.bank_account}` : null],
  ]
  return (
    <section className="ticket" style={{ display: 'grid', gap: 12 }}>
      <header className="ticket-head">
        <div>
          <h2>{o.display_name}</h2>
          <span className="muted small">
            {[o.is_buyer && t('role_buyer'), o.is_seller && t('role_seller')].filter(Boolean).join(' + ')} · {VERIFICATION[o.verification_status][lang]}
          </span>
        </div>
        <span className={o.registry_source === 'manual' ? 'pill lamp' : 'pill plain'}>
          {o.registry_source === 'manual' ? `✎ ${t('typed_by_hand')}` : `✓ ${o.registry_source}`}
        </span>
      </header>
      <dl className="sums">
        {rows.map(([k, val]) => (
          <div key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd style={{ fontWeight: 500 }}>{val || '—'}</dd>
          </div>
        ))}
      </dl>
      <ul className="lines">
        {v.files.map((f) => (
          <li key={f.id} className="line">
            <div className="line-main">
              <div>
                <div className="line-name">{FILE_KINDS[f.kind][lang]}</div>
                <button className="link small" onClick={() => void openFile(`/files/${f.id}`)}>
                  📄 {f.file_name}
                </button>
                <div className="line-sub">{dateTime(f.created_at, lang)}</div>
              </div>
              <span className={f.status === 'approved' ? 'pill brand' : f.status === 'rejected' ? 'pill plain' : 'pill lamp'}>
                {FILE_STATUS[f.status][lang]}
              </span>
            </div>
            {f.status === 'pending' && (
              <div className="line-tools" style={{ justifyContent: 'flex-end' }}>
                <button className="btn ghost" style={{ minHeight: 40 }} onClick={() => run(api.reviewFile(f.id, false, note || undefined))}>
                  {t('reject')}
                </button>
                <button className="btn primary" style={{ minHeight: 40 }} onClick={() => run(api.reviewFile(f.id, true))}>
                  {t('approve')}
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <label className="field">
        <span>{t('note')}</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      {o.verification_status !== 'verified' && (
        <div className="actions">
          <button className="btn ghost" onClick={() => run(api.reviewOrg(o.id, false, note || undefined))}>
            {t('reject')}
          </button>
          <button className="btn accent" onClick={() => run(api.reviewOrg(o.id, true, note || undefined))}>
            {t('approve_company')}
          </button>
        </div>
      )}
    </section>
  )
}

/** What each seeded person is good for (app/seed.py, app/demo.py). */
const DEMO_STORY: Record<string, Record<'hy' | 'ru' | 'en', string>> = {
  'arax@easo.local': {
    hy: 'Սրճարանի սեփականատեր․ կատալոգ, զամբյուղ, սակարկություն, պատվերներ',
    ru: 'Владелец кафе: каталог, корзина, торг. Ждёт ответа на встречную цену, есть оплаченный заказ',
    en: 'Cafe owner: catalog, cart, haggling. A counter-offer awaits an answer; one order is paid',
  },
  'chef@easo.local': {
    hy: 'Խոհարար․ պատվիրում է առանց սակարկելու, սահմանաչափ 50 000 ֏',
    ru: 'Шеф того же кафе: заказывает без торга, лимит заказа 50 000 ֏',
    en: 'Chef at the same cafe: orders without haggling, 50 000 ֏ limit',
  },
  'nova@easo.local': {
    hy: 'Բարի սեփականատեր․ պատվերը ճանապարհին է, առաջարկը սպասում է վաճառողին',
    ru: 'Владелец бара: заказ в пути, своё предложение цены ждёт продавца',
    en: 'Bar owner: an order on its way, a price offer waiting for the seller',
  },
  'lori@easo.local': {
    hy: 'Պանրի արտադրող․ պետք է պատասխանել գնի առաջարկին, ավտոպիլոտ',
    ru: 'Сыроварня: нужно ответить на предложение цены, автопилот торга, свои клиенты',
    en: 'Cheese maker: a price offer to answer, haggling autopilot, own clients',
  },
  'manager@easo.local': {
    hy: 'Նույն պանրագործարանի մենեջեր (առանց սեփականատիրոջ իրավունքների)',
    ru: 'Менеджер той же сыроварни: работает с заказами, без прав владельца',
    en: 'Manager at the same cheese maker: handles orders, no owner rights',
  },
  'pack@easo.local': {
    hy: 'Մեծածախ մատակարար․ նոր պատվեր հացատնից, մեկը ճանապարհին',
    ru: 'Оптовик расходников: новый заказ от пекарни, один заказ в пути',
    en: 'Wholesaler: a new order from the bakery, one on its way',
  },
  'farm@easo.local': {
    hy: 'Ֆերմա․ բանջարեղեն, պատվերներ դեռ չկան',
    ru: 'Ферма: овощи и зелень, заказов пока нет',
    en: 'Farm: vegetables and greens, no orders yet',
  },
  'baker@easo.local': {
    hy: 'Հացատուն, որը և՛ գնում է, և՛ վաճառում․ փորձեք «Գնում եմ / Վաճառում եմ»',
    ru: 'Пекарня, которая и покупает, и продаёт: переключатель «Покупаю / Продаю»',
    en: 'Bakery that both buys and sells: try the Buying / Selling switch',
  },
  'honey@easo.local': {
    hy: 'Նոր վաճառող, սպասում է ստուգման․ ապրանքները կատալոգում չեն',
    ru: 'Новый продавец ждёт проверки: товары не видны в каталоге, есть в очереди модерации',
    en: 'New seller waiting for review: products hidden, in the moderation queue',
  },
  'newbie@easo.local': {
    hy: 'Մուտք է գործել, ընկերություն չկա․ գրանցում ՀՎՀՀ-ով',
    ru: 'Вошёл, но компании нет: регистрация по ИНН',
    en: 'Signed in, no company yet: sign-up by TIN',
  },
  'admin@easo.local': { hy: 'Դեմո օպերատոր', ru: 'Демо-оператор', en: 'Demo operator' },
}

function DemoUsers() {
  const t = useT()
  const lang = useLang()
  const navigate = useNavigate()
  const { signIn } = useSession()
  const [users, setUsers] = useState<DemoUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.demoUsers().then(setUsers, (e: ApiError) => setError(problemText(e.code, lang)))
  }, [lang])

  async function enter(u: DemoUser) {
    try {
      const { access_token } = await api.demoUserToken(u.id)
      await signIn(access_token)
      navigate('/', { replace: true })
    } catch (e) {
      setError(problemText((e as ApiError).code, lang))
    }
  }

  return (
    <>
      <p className="muted small" style={{ margin: '0 4px' }}>
        {t('demo_hint')}
      </p>
      {error && <p className="error">{error}</p>}
      {users?.length === 0 && <p className="col-empty">{t('queue_empty')}</p>}
      <ul className="demo-users">
        {users?.map((u) => (
          <li key={u.id}>
            <button onClick={() => void enter(u)} title={`${t('sign_in_as')} ${u.email}`}>
              <span style={{ display: 'grid', gap: 2, textAlign: 'left' }}>
                <span>
                  {u.company ?? u.name}
                  {u.roles.length > 0 && (
                    <small> · {u.roles.map((r) => (r === 'buyer' ? t('role_buyer') : t('role_seller'))).join(' + ')}</small>
                  )}
                </span>
                <small>{DEMO_STORY[u.email]?.[lang] ?? u.name}</small>
              </span>
              <small>{u.email.replace('@easo.local', '')}</small>
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}
