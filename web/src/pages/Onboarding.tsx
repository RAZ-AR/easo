import type React from 'react'
import { useEffect, useState } from 'react'
import { AddressField } from '../components/AddressField'
import { ContactField } from '../components/ContactField'
import { ConsentText } from '../components/Consent'
import { Field, SelectField } from '../components/Field'
import { fill, problemText, useLang, useT } from '../i18n'
import { api, ApiError, type Lookup, type Mode, type NewOrg, type TaxRegime } from '../lib/api'
import { pendingInvite } from '../lib/invite'
import { useSession } from '../lib/session'

const VENUES = ['cafe', 'restaurant', 'bar', 'bakery', 'hotel', 'catering', 'other']
const VENUE_LABEL: Record<string, Record<'hy' | 'ru' | 'en', string>> = {
  cafe: { hy: 'Սրճարան', ru: 'Кафе', en: 'Cafe' },
  restaurant: { hy: 'Ռեստորան', ru: 'Ресторан', en: 'Restaurant' },
  bar: { hy: 'Բար', ru: 'Бар', en: 'Bar' },
  bakery: { hy: 'Հացատուն', ru: 'Пекарня', en: 'Bakery' },
  hotel: { hy: 'Հյուրանոց', ru: 'Отель', en: 'Hotel' },
  catering: { hy: 'Քեյթրինգ', ru: 'Кейтеринг', en: 'Catering' },
  other: { hy: 'Այլ', ru: 'Другое', en: 'Other' },
}

type Step = 'role' | 'tin' | 'form'

/** First run without a company: pick a role, find the company by TIN, confirm the requisites. */
export function Onboarding() {
  const { reload } = useSession()
  const t = useT()
  const lang = useLang()
  const [step, setStep] = useState<Step>('role')
  // Came through a supplier's link: say whose client the new company becomes
  const [supplier, setSupplier] = useState<string | null>(null)
  useEffect(() => {
    const code = pendingInvite.get()
    if (code) api.supplier(code).then((p) => setSupplier(p.display_name), () => pendingInvite.clear())
  }, [])
  const [form, setForm] = useState<NewOrg>({ roles: ['buyer'], tin: '', legal_name: '', venue_type: 'cafe' })
  const [lookup, setLookup] = useState<Lookup | null>(null)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  const set = (patch: Partial<NewOrg>) => setForm((f) => ({ ...f, ...patch }))
  const bind = (key: keyof NewOrg) => ({
    value: (form[key] as string | null | undefined) ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => set({ [key]: e.target.value }),
    error: errors[key as string],
  })

  async function find() {
    setBusy(true)
    setError(null)
    try {
      const r = await api.lookup(form.tin)
      setLookup(r)
      const c = r.company
      set({
        tin: r.tin,
        ...(c && {
          legal_name: c.legal_name,
          display_name: c.legal_name.split('/').at(-1)?.trim(),
          registration_number: c.registration_number,
          legal_address: c.legal_address,
          director_name: c.director_name,
          tax_regime: c.tax_regime,
        }),
      })
      if (!r.already_registered && (!c || c.active)) setStep('form')
    } catch (e) {
      setError(problemText((e as ApiError).code === 'validation_failed' ? 'bad_tin' : (e as ApiError).code, lang))
    } finally {
      setBusy(false)
    }
  }

  async function create(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setErrors({})
    try {
      const invite = form.roles.includes('buyer') ? pendingInvite.get() : null
      await api.createOrg({ ...form, invite_code: invite })
      // The app switches to the company's cabinet; /onboarding there leads on (AfterSignUp in App)
      await reload()
    } catch (err) {
      const ex = err as ApiError
      const byField: Record<string, string> = {}
      for (const p of ex.problems ?? []) if (typeof p.field === 'string') byField[p.field] = problemText(p.code, lang)
      setErrors(byField)
      if (!Object.keys(byField).length) setError(problemText(ex.code, lang))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login page">
      <h1 className="tagline" style={{ marginTop: 8 }}>
        {t('onb_title')}
      </h1>
      {supplier && form.roles.includes('buyer') && (
        <p className="pill lamp self-start">{fill(t('sup_onb_note'), { name: supplier })}</p>
      )}

      {step === 'role' && (
        <div className="stack">
          <p className="section-title">{t('onb_role')}</p>
          {(['buyer', 'seller'] as Mode[]).map((r) => {
            const on = form.roles.includes(r)
            return (
              <button
                key={r}
                className={on ? 'reorder-card role-card on' : 'reorder-card role-card'}
                style={{ width: '100%', padding: 24 }}
                aria-pressed={on}
                onClick={() =>
                  set({ roles: on ? form.roles.filter((x) => x !== r) : [...form.roles, r] })
                }
              >
                <span className="ibtn" aria-hidden>
                  {on ? '✓' : '+'}
                </span>
                <span className="big">{r === 'buyer' ? t('role_buyer') : t('role_seller')}</span>
                <span className="names">{r === 'buyer' ? t('role_buyer_sub') : t('role_seller_sub')}</span>
              </button>
            )
          })}
          <button className="btn primary block" disabled={form.roles.length === 0} onClick={() => setStep('tin')}>
            {t('continue')} →
          </button>
        </div>
      )}

      {step === 'tin' && (
        <section className="ticket" style={{ display: 'grid', gap: 12 }}>
          <span className="pill brand" style={{ justifySelf: 'start' }}>
            {form.roles.map((r) => (r === 'buyer' ? t('role_buyer') : t('role_seller'))).join(' + ')}
          </span>
          <Field
            label={t('tin')}
            hint={t('tin_hint')}
            inputMode="numeric"
            autoFocus
            placeholder="02744119"
            value={form.tin}
            onChange={(e) => set({ tin: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && find()}
          />
          <button className="btn primary block" disabled={busy || form.tin.replace(/\D/g, '').length < 8} onClick={find}>
            {t('find')}
          </button>
          {lookup?.already_registered && <p className="warn">{t('already_on_easo')}</p>}
          {lookup?.company && !lookup.company.active && <p className="warn">{t('company_closed')}</p>}
          {lookup && !lookup.found && !lookup.already_registered && (
            <button className="link small" onClick={() => setStep('form')}>
              {lookup.unavailable ? t('registry_unavailable') : t('not_found_manual')} →
            </button>
          )}
          {error && <p className="error">{error}</p>}
          <button className="link" onClick={() => setStep('role')}>
            ← {t('back_step')}
          </button>
        </section>
      )}

      {step === 'form' && (
        <form className="ticket" style={{ display: 'grid', gap: 12 }} onSubmit={create}>
          {lookup?.found ? (
            <div className="next" style={{ padding: 14 }}>
              <span>
                <strong style={{ fontSize: 15 }}>✓ {t('found_in')}</strong>
                {lookup.provider === 'demo' && <span className="muted small">{t('demo_data')}</span>}
                {lookup.provider === 'eregister' && <span className="muted small">{t('eregister_source')}</span>}
              </span>
              <span className="clock">{form.tin}</span>
            </div>
          ) : (
            <p className="warn">{lookup?.unavailable ? t('registry_unavailable') : t('not_found_manual')}</p>
          )}
          {lookup?.found && lookup.provider === 'eregister' && <p className="muted small">{t('fill_missing')}</p>}
          <Field label={t('legal_name')} required {...bind('legal_name')} />
          <Field label={t('display_name')} {...bind('display_name')} />
          <SelectField
            label={t('tax_regime')}
            value={form.tax_regime ?? ''}
            onChange={(e) => set({ tax_regime: (e.target.value || null) as TaxRegime | null })}
            options={[
              { value: '', label: '—' },
              { value: 'vat', label: t('regime_vat') },
              { value: 'turnover', label: t('regime_turnover') },
              { value: 'micro', label: t('regime_micro') },
            ]}
          />
          <AddressField label={t('legal_address')} value={form.legal_address ?? ''} onChange={(v) => set({ legal_address: v })} error={errors.legal_address} />
          <Field label={t('director')} {...bind('director_name')} />
          <Field label={t('reg_number')} {...bind('registration_number')} />
          <ContactField kind="phone" label={t('phone')} value={form.phone ?? ''} onChange={(v) => set({ phone: v })} error={errors.phone} />
          <ContactField kind="email" label={t('email')} value={form.email ?? ''} onChange={(v) => set({ email: v })} error={errors.email} />
          {form.roles.includes('buyer') && (
            <>
              <SelectField
                label={t('venue_type')}
                value={form.venue_type ?? 'cafe'}
                onChange={(e) => set({ venue_type: e.target.value })}
                options={VENUES.map((v) => ({ value: v, label: VENUE_LABEL[v][lang] }))}
              />
              <p className="muted small">{t('addresses_later')}</p>
            </>
          )}
          {form.roles.includes('seller') && (
            <>
              <Field label={t('bank_name')} {...bind('bank_name')} />
              <ContactField
                kind="bank_account"
                label={t('bank_account')}
                value={form.bank_account ?? ''}
                onChange={(v) => set({ bank_account: v })}
                error={errors.bank_account}
              />
            </>
          )}
          <label className="check consent">
            <input type="checkbox" checked={!!form.accept_terms} onChange={(e) => set({ accept_terms: e.target.checked })} />
            <span>
              <ConsentText />
            </span>
          </label>
          {errors.accept_terms && <p className="error">{errors.accept_terms}</p>}
          {error && <p className="error">{error}</p>}
          <button className="btn primary block" disabled={busy || !form.accept_terms}>
            {t('create_company')} →
          </button>
          <button type="button" className="link" onClick={() => setStep('tin')}>
            ← {t('back_step')}
          </button>
        </form>
      )}
    </div>
  )
}
