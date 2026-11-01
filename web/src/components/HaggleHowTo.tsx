import { fill, useT } from '../i18n'

const KEY = 'easo.haggle.howto.seen'

export function howToSeen(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

/** The rules of haggling in four steps, shown before the buyer's first offer. */
export function HaggleHowTo({ hours, rounds, onClose }: { hours: number; rounds: number; onClose: () => void }) {
  const t = useT()
  const close = () => {
    try {
      localStorage.setItem(KEY, '1')
    } catch {
      /* private mode: show it again next time */
    }
    onClose()
  }
  return (
    <div className="howto" role="note">
      <strong>⇄ {t('howto_title')}</strong>
      <ol>
        <li>{t('howto_1')}</li>
        <li>{fill(t('howto_2'), { h: hours })}</li>
        <li>{fill(t('howto_3'), { n: rounds })}</li>
        <li>{t('howto_4')}</li>
      </ol>
      <button type="button" className="btn small" onClick={close}>
        {t('howto_got')}
      </button>
    </div>
  )
}
