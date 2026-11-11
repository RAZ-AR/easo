import { useT } from '../i18n'
import { qty as fmtQty } from '../lib/format'

/** −/+ with the product's step. Going below the minimum quantity means 0 (remove). */
export function QtyStepper({
  value,
  min,
  step,
  onChange,
  disabled,
  unitLabel,
}: {
  value: string
  min: string
  step: string
  onChange: (next: string) => void
  disabled?: boolean
  unitLabel?: string
}) {
  const t = useT()
  const v = Number(value)
  const s = Number(step)
  const m = Number(min)
  // Avoid 0.1 + 0.2 style float noise: steps are at most 3 decimals
  const round = (x: number) => Math.round(x * 1000) / 1000
  const down = round(v - s) < m ? 0 : round(v - s)
  const up = v < m ? m : round(v + s)
  return (
    <div className="stepper">
      <button type="button" aria-label={t('qty_less')} disabled={disabled} onClick={() => onChange(String(down))}>
        −
      </button>
      <span>
        {fmtQty(value)}
        {unitLabel && <span className="u">{unitLabel}</span>}
      </span>
      <button type="button" aria-label={t('qty_more')} disabled={disabled} onClick={() => onChange(String(up))}>
        +
      </button>
    </div>
  )
}
