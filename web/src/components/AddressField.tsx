import { useEffect, useId, useRef, useState } from 'react'
import { useLang, useT } from '../i18n'
import { api, type Place } from '../lib/api'
import { loadStreets, splitHouse, streetName, suggest, type Street } from '../lib/streets'

/**
 * Address with Yerevan street suggestions as you type (our own list, instant), then a check of the full
 * address with the house number on the map: shows what was found and hands over the district.
 */
export function AddressField({
  label,
  value,
  onChange,
  onResolved,
  onDistrict,
  required,
  error,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  /** The address was found on the map: its district (Easo name) and coordinates */
  onResolved?: (place: Place) => void
  /** A street was picked that lies in one district only */
  onDistrict?: (district: string) => void
  required?: boolean
  error?: string
}) {
  const t = useT()
  const lang = useLang()
  const id = useId()
  const [streets, setStreets] = useState<Street[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [check, setCheck] = useState<{ state: 'found' | 'missing'; text: string } | null>(null)
  const checked = useRef<string>('')

  useEffect(() => {
    void loadStreets().then(setStreets)
  }, [])

  const options = open ? suggest(streets, value) : []

  function pick(s: Street) {
    const { house } = splitHouse(value)
    onChange(`${streetName(s, lang)} ${house ?? ''}`)
    if (s.d.length === 1) onDistrict?.(s.d[0])
    setOpen(false)
    setCheck(null)
  }

  async function resolve() {
    const text = value.trim()
    if (!splitHouse(text).house || text === checked.current) return
    checked.current = text
    try {
      const r = await api.geoResolve(text, lang)
      if (r.unavailable) return setCheck(null) // the map is down: the address stays as typed
      if (r.found && r.text) {
        setCheck({ state: 'found', text: [r.text, r.district].filter(Boolean).join(' · ') })
        onResolved?.(r)
      } else setCheck({ state: 'missing', text: '' })
    } catch {
      setCheck(null)
    }
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!options.length) {
      if (e.key === 'Enter') void resolve()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => (i + 1) % options.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i - 1 + options.length) % options.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      pick(options[active])
    } else if (e.key === 'Escape') setOpen(false)
  }

  return (
    <div className="address-field">
      <label className="field">
        <span>{label}</span>
        <input
          role="combobox"
          aria-expanded={options.length > 0}
          aria-controls={`${id}-list`}
          aria-activedescendant={options.length ? `${id}-${active}` : undefined}
          aria-autocomplete="list"
          autoComplete="off"
          required={required}
          placeholder={t('address_placeholder')}
          value={value}
          onChange={(e) => (onChange(e.target.value), setOpen(true), setActive(0), setCheck(null))}
          onFocus={() => setOpen(true)}
          onBlur={() => (setTimeout(() => setOpen(false), 150), void resolve())}
          onKeyDown={onKey}
        />
        {error && <span className="error small">{error}</span>}
      </label>
      {options.length > 0 && (
        <ul className="address-options" role="listbox" id={`${id}-list`}>
          {options.map((s, i) => (
            <li
              key={s.hy}
              id={`${id}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'on' : ''}
              onMouseDown={(e) => (e.preventDefault(), pick(s))}
            >
              <span>{streetName(s, lang)}</span>
              {s.d.length > 0 && <span className="muted small">{s.d.join(', ')}</span>}
            </li>
          ))}
          <li className="osm-credit" aria-hidden>
            © OpenStreetMap
          </li>
        </ul>
      )}
      {check?.state === 'found' && <p className="address-check ok">✓ {check.text}</p>}
      {check?.state === 'missing' && <p className="address-check">{t('address_not_on_map')}</p>}
    </div>
  )
}
