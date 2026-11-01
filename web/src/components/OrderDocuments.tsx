import { useCallback, useEffect, useState } from 'react'
import { problemText, useLang, useT } from '../i18n'
import { api, ApiError, type Order, type QDocument } from '../lib/api'
import { date, tr } from '../lib/format'

const PRINTABLE = ['invoice', 'delivery_note', 'acceptance_act'] as const
const CONFIRMED = ['confirmed', 'delivering', 'delivered', 'accepted', 'disputed']

/** Documents of an order: Easo printouts, plus the numbers of the SRC e-invoice and e-waybill. */
export function OrderDocuments({ order }: { order: Order }) {
  const t = useT()
  const lang = useLang()
  const [docs, setDocs] = useState<QDocument[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isSeller = order.my_side === 'seller'
  const confirmed = CONFIRMED.includes(order.status)

  const load = useCallback(() => api.documents(order.id).then(setDocs, () => setDocs([])), [order.id])
  useEffect(() => {
    void load()
  }, [load, order.status])

  if (!confirmed || !docs) return null
  const printable = docs.filter((d) => (PRINTABLE as readonly string[]).includes(d.kind))
  const src = (kind: 'tax_invoice' | 'waybill') => docs.find((d) => d.kind === kind)

  return (
    <section className="ticket">
      <header className="ticket-head">
        <h2>🧾 {t('documents')}</h2>
      </header>
      <ul className="lines">
        {printable.map((d) => (
          <li key={d.id} className="line">
            <div className="line-main">
              <div>
                <div className="line-name">{d.payload ? tr(d.payload.title, lang) : d.kind}</div>
                <div className="line-sub num">
                  {d.number} {d.issued_at && `· ${date(d.issued_at, lang)}`}
                </div>
              </div>
              <a className="ibtn" href={`/print/${d.id}`} target="_blank" rel="noopener" aria-label={t('print')} title={t('print')}>
                ↗
              </a>
            </div>
          </li>
        ))}
      </ul>
      {printable.length === 0 && isSeller && (
        <button className="btn block" onClick={() => api.issueDocuments(order.id).then(setDocs, (e: ApiError) => setError(problemText(e.code, lang)))}>
          {t('issue_docs')}
        </button>
      )}
      <hr className="rule" />
      <div className="field" style={{ gap: 4 }}>
        <span>{t('src_title')}</span>
        {isSeller && <span style={{ fontWeight: 500 }}>{t('src_hint')}</span>}
      </div>
      <SrcNumber orderId={order.id} kind="tax_invoice" label={t('src_invoice_no')} doc={src('tax_invoice')} editable={isSeller} onSaved={load} onError={setError} />
      <SrcNumber orderId={order.id} kind="waybill" label={t('src_waybill_no')} doc={src('waybill')} editable={isSeller} onSaved={load} onError={setError} />
      {error && <p className="error small">{error}</p>}
    </section>
  )
}

function SrcNumber({
  orderId,
  kind,
  label,
  doc,
  editable,
  onSaved,
  onError,
}: {
  orderId: string
  kind: 'tax_invoice' | 'waybill'
  label: string
  doc?: QDocument
  editable: boolean
  onSaved: () => Promise<void>
  onError: (m: string | null) => void
}) {
  const t = useT()
  const lang = useLang()
  const [value, setValue] = useState(doc?.external_id ?? '')
  useEffect(() => setValue(doc?.external_id ?? ''), [doc?.external_id])

  if (!editable) {
    return (
      <div className="line-tools">
        <span className="muted small">{label}</span>
        <b className="num">{doc?.external_id ?? '—'}</b>
      </div>
    )
  }
  const save = async () => {
    onError(null)
    try {
      await api.srcNumber(orderId, kind, value)
      await onSaved()
    } catch (e) {
      const ex = e as ApiError
      onError(problemText(ex.problems?.[0]?.code ?? ex.code, lang))
    }
  }
  return (
    <div className="dial-row" style={{ marginTop: 8 }}>
      <label className="field" style={{ flex: 1 }}>
        <span>{label}</span>
        <input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && save()} />
      </label>
      <button className="ibtn dark" style={{ alignSelf: 'end', marginBottom: 4 }} disabled={!value.trim() || value === doc?.external_id} onClick={save} aria-label={t('save')}>
        ✓
      </button>
    </div>
  )
}
