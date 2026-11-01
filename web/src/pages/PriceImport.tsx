import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { problemText, TEXTS, useLang, useT } from '../i18n'
import {
  api,
  ApiError,
  type ImportAction,
  type ImportBody,
  type ImportField,
  type ImportItem,
  type ImportResult,
  type ParsedTable,
  type Unit,
} from '../lib/api'
import { amd, tr, unit } from '../lib/format'

const FIELDS: ImportField[] = ['sku', 'name_hy', 'name_ru', 'name_en', 'price', 'unit', 'in_stock', 'stock_qty', 'min_qty', 'pack_size']
const UNITS: Unit[] = ['kg', 'l', 'pcs', 'pack']
const ORDER: ImportAction[] = ['error', 'update', 'create', 'out_of_stock', 'same']

const TEMPLATE =
  'Артикул;Название (арм.);Название (рус.);Название (англ.);Цена;Единица;Остаток\n' +
  'L-1;Լոռի պանիր;Сыр лори;Lori cheese;3500;кг;25\n'

type Step = { kind: 'file' } | { kind: 'map'; table: ParsedTable } | { kind: 'preview'; table: ParsedTable; result: ImportResult } | { kind: 'done'; result: ImportResult }

/** Seller cabinet: a price list from Excel or CSV in four steps — file, columns, preview, done. */
export function PriceImport() {
  const t = useT()
  const lang = useLang()
  const [step, setStep] = useState<Step>({ kind: 'file' })
  const [mapping, setMapping] = useState<Record<string, ImportField>>({})
  const [defaultUnit, setDefaultUnit] = useState<Unit | null>(null)
  const [missingOut, setMissingOut] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const input = useRef<HTMLInputElement>(null)

  const fail = (err: unknown) => {
    const ex = err as ApiError
    setErrors(ex.problems?.length ? ex.problems.map((p) => problemText(p.code, lang)) : [problemText(ex.code ?? 'error', lang)])
  }
  async function run(job: () => Promise<void>) {
    setBusy(true)
    setErrors([])
    try {
      await job()
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  const body = (table: ParsedTable): ImportBody => ({ mapping, rows: table.rows, default_unit: defaultUnit, missing_out_of_stock: missingOut })

  function onFile(file: File | undefined) {
    if (!file) return
    void run(async () => {
      const table = await api.parsePriceList(file)
      setMapping(table.mapping)
      setStep({ kind: 'map', table })
    })
  }

  function downloadTemplate() {
    // BOM so Excel opens the Armenian and Russian letters correctly
    const url = URL.createObjectURL(new Blob(['﻿' + TEMPLATE], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'easo-price-template.csv'
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }

  return (
    <div className="stack page">
      <div className="page-head">
        <h1 className="page-title">{t('import_title')}</h1>
        <Link className="link small" to="/seller/products">
          ← {t('tab_products')}
        </Link>
      </div>
      <ol className="import-steps" aria-label={t('import_title')}>
        {(['file', 'map', 'preview', 'done'] as const).map((k, i) => (
          <li key={k} className={step.kind === k ? 'on' : ''}>
            {i + 1}
          </li>
        ))}
      </ol>

      {step.kind === 'file' && (
        <section className="ticket import-drop">
          <p>{t('import_lead')}</p>
          <p className="muted small">{t('import_match_hint')}</p>
          <div className="actions">
            <button className="btn primary" disabled={busy} onClick={() => input.current?.click()}>
              {busy ? t('loading') : `↑ ${t('import_choose')}`}
            </button>
            <button className="btn ghost" type="button" onClick={downloadTemplate}>
              ↓ {t('import_template')}
            </button>
          </div>
          <input ref={input} type="file" accept=".xlsx,.csv,.txt" hidden onChange={(e) => onFile(e.target.files?.[0])} />
        </section>
      )}

      {step.kind === 'map' && (
        <section className="ticket">
          <header className="ticket-head">
            <h2>{t('import_columns')}</h2>
            <span className="muted small num">
              {step.table.rows.length} {t('import_rows')}
            </span>
          </header>
          <p className="muted small">{t('import_columns_hint')}</p>
          <ul className="import-cols">
            {step.table.headers.map((h, i) => {
              const samples = step.table.rows
                .slice(0, 3)
                .map((r) => r[i])
                .filter((v) => v !== null && v !== '')
              return (
                <li key={i} className={mapping[i] ? 'mapped' : ''}>
                  <div className="import-col-head">
                    <strong>{h || `#${i + 1}`}</strong>
                    <span className="muted small">{samples.join(' · ') || '—'}</span>
                  </div>
                  <select
                    aria-label={h || `#${i + 1}`}
                    value={mapping[i] ?? ''}
                    onChange={(e) => {
                      const next = { ...mapping }
                      if (e.target.value) next[i] = e.target.value as ImportField
                      else delete next[i]
                      setMapping(next)
                    }}
                  >
                    <option value="">{t('import_skip')}</option>
                    {FIELDS.map((f) => (
                      <option key={f} value={f}>
                        {TEXTS[`f_${f}`][lang]}
                      </option>
                    ))}
                  </select>
                </li>
              )
            })}
          </ul>
          <div className="import-options">
            <label className="field">
              <span>{t('import_default_unit')}</span>
              <select value={defaultUnit ?? ''} onChange={(e) => setDefaultUnit((e.target.value || null) as Unit | null)}>
                <option value="">—</option>
                {UNITS.map((u) => (
                  <option key={u} value={u}>
                    {unit(u, lang)}
                  </option>
                ))}
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={missingOut} onChange={(e) => setMissingOut(e.target.checked)} /> {t('import_missing')}
            </label>
          </div>
          <div className="actions">
            <button className="btn ghost" onClick={() => setStep({ kind: 'file' })}>
              {t('back')}
            </button>
            <button
              className="btn primary"
              disabled={busy}
              onClick={() => void run(async () => setStep({ kind: 'preview', table: step.table, result: await api.previewImport(body(step.table)) }))}
            >
              {t('import_check')} →
            </button>
          </div>
        </section>
      )}

      {step.kind === 'preview' && (
        <Preview
          result={step.result}
          busy={busy}
          onBack={() => setStep({ kind: 'map', table: step.table })}
          onApply={() => void run(async () => setStep({ kind: 'done', result: await api.applyImport(body(step.table)) }))}
        />
      )}

      {step.kind === 'done' && (
        <section className="ticket import-done">
          <h2>✓ {t('import_done')}</h2>
          <Counts counts={step.result.counts} done />
          <div className="actions">
            <button className="btn ghost" onClick={() => setStep({ kind: 'file' })}>
              {t('import_another')}
            </button>
            <Link className="btn primary" to="/seller/products">
              {t('import_to_products')}
            </Link>
          </div>
        </section>
      )}

      {errors.map((e) => (
        <p key={e} className="error">
          {e}
        </p>
      ))}
    </div>
  )
}

const COUNT_LABEL: Record<ImportAction, 'import_new' | 'import_update' | 'import_same' | 'import_errors' | 'import_oos'> = {
  create: 'import_new',
  update: 'import_update',
  same: 'import_same',
  error: 'import_errors',
  out_of_stock: 'import_oos',
}

const DONE_LABEL: Record<ImportAction, 'import_added' | 'import_updated' | 'import_same' | 'import_skipped' | 'import_oos_done'> = {
  create: 'import_added',
  update: 'import_updated',
  same: 'import_same',
  error: 'import_skipped',
  out_of_stock: 'import_oos_done',
}

function Counts({
  counts,
  filter,
  onFilter,
  done = false,
}: {
  counts: Record<ImportAction, number>
  filter?: ImportAction | null
  onFilter?: (a: ImportAction | null) => void
  done?: boolean
}) {
  const t = useT()
  return (
    <div className="import-counts">
      {(['create', 'update', 'out_of_stock', 'same', 'error'] as ImportAction[])
        .filter((a) => counts[a] > 0 || a === 'create' || a === 'update')
        .map((a) => (
          <button
            key={a}
            type="button"
            className={`import-count is-${a} ${filter === a ? 'on' : ''}`}
            disabled={!onFilter}
            onClick={() => onFilter?.(filter === a ? null : a)}
          >
            <b className="num">{counts[a]}</b>
            <span>{t(done ? DONE_LABEL[a] : COUNT_LABEL[a])}</span>
          </button>
        ))}
    </div>
  )
}

function Preview({ result, busy, onBack, onApply }: { result: ImportResult; busy: boolean; onBack: () => void; onApply: () => void }) {
  const t = useT()
  const [filter, setFilter] = useState<ImportAction | null>(null)
  const items = useMemo(
    () =>
      result.items
        .filter((i) => (filter ? i.action === filter : i.action !== 'same'))
        .sort((a, b) => ORDER.indexOf(a.action) - ORDER.indexOf(b.action) || (a.line ?? 1e9) - (b.line ?? 1e9)),
    [result, filter],
  )
  const c = result.counts
  const writes = c.create + c.update + c.out_of_stock
  return (
    <section className="ticket">
      <Counts counts={c} filter={filter} onFilter={setFilter} />
      {items.length === 0 && <p className="muted">{t('import_nothing')}</p>}
      <ul className="import-items">
        {items.slice(0, 300).map((item, i) => (
          <ItemRow key={`${item.line}-${item.product_id}-${i}`} item={item} />
        ))}
      </ul>
      {c.error > 0 && writes > 0 && <p className="hint muted">{t('import_errors_skipped')}</p>}
      <div className="actions">
        <button className="btn ghost" onClick={onBack}>
          {t('back')}
        </button>
        <button className="btn primary" disabled={busy || writes === 0} onClick={onApply}>
          {t('import_apply')} · {writes}
        </button>
      </div>
    </section>
  )
}

function ItemRow({ item }: { item: ImportItem }) {
  const t = useT()
  const lang = useLang()
  const name = tr(item.name, lang) || item.sku || '—'
  return (
    <li className={`import-item is-${item.action}`}>
      <div className="import-item-head">
        <span className="import-tag">{t(COUNT_LABEL[item.action])}</span>
        <strong>{item.action === 'out_of_stock' ? '' : name}</strong>
        {item.sku && <span className="muted small mono">{item.sku}</span>}
        {item.line !== null && (
          <span className="muted small num import-line">
            {t('import_line')} {item.line}
          </span>
        )}
      </div>
      {item.action === 'create' && item.price_amd !== null && (
        <div className="small num">
          {amd(item.price_amd)}
          {item.unit && ` / ${unit(item.unit, lang)}`}
        </div>
      )}
      {item.changes.length > 0 && (
        <ul className="import-changes small">
          {item.changes.map((ch) => (
            <li key={ch.field}>
              <Change field={ch.field} old={ch.old} next={ch.new} />
            </li>
          ))}
        </ul>
      )}
      {item.problems.map((p) => (
        <p key={p} className="error small">
          {problemText(p, lang)}
        </p>
      ))}
      {item.warnings.map((w) => (
        <p key={w} className="import-warn">
          {TEXTS[`w_${w}` as 'w_price_jump']?.[lang] ?? w}
        </p>
      ))}
    </li>
  )
}

function Change({ field, old, next }: { field: string; old: unknown; next: unknown }) {
  const t = useT()
  const lang = useLang()
  const show = (v: unknown): string => {
    if (v === null || v === undefined || v === '') return '—'
    if (field === 'price_amd') return amd(Number(v))
    if (field === 'unit') return unit(v as Unit, lang)
    if (field === 'in_stock') return v ? t('in_stock') : t('out_of_stock')
    if (field === 'name') return tr(v as Record<string, string>, lang)
    return String(v)
  }
  if (field === 'archived') return <span>{t('import_restored')}</span>
  const label: Record<string, string> = {
    price_amd: t('f_price'),
    name: t('f_name'),
    unit: t('f_unit'),
    in_stock: t('in_stock'),
    min_qty: t('f_min_qty'),
    pack_size: t('f_pack_size'),
    sku: t('f_sku'),
  }
  return (
    <span>
      {label[field] ?? field}: <s className="muted">{show(old)}</s> → <b>{show(next)}</b>
    </span>
  )
}
