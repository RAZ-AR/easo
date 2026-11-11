import type { I18nText, Lang, Unit } from './api'

/** 3500 -> "3 500 ֏" (narrow no-break space as thousands separator, like Armenian receipts). */
export function amd(value: number): string {
  const digits = Math.round(value).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return `${digits} ֏`
}

/** Text in the user's language, falling back to Armenian, Russian, English. */
export function tr(text: I18nText, lang: Lang): string {
  return text[lang] || text.hy || text.ru || text.en || ''
}

/** "20.000" -> "20", "0.500" -> "0.5" */
export function qty(value: string | number): string {
  return Number(value).toString()
}

const UNITS: Record<Unit, Record<Lang, string>> = {
  kg: { hy: 'կգ', ru: 'кг', en: 'kg' },
  l: { hy: 'լ', ru: 'л', en: 'l' },
  pcs: { hy: 'հատ', ru: 'шт', en: 'pcs' },
  pack: { hy: 'տուփ', ru: 'уп', en: 'pack' },
}

export function unit(u: Unit, lang: Lang): string {
  return UNITS[u][lang]
}

const LOCALES: Record<Lang, string> = { hy: 'hy-AM', ru: 'ru-RU', en: 'en-GB' }

// Some browsers ship without Armenian locale data and fall back to English, so these are spelled out.
const HY_MONTHS = ['հունվարի', 'փետրվարի', 'մարտի', 'ապրիլի', 'մայիսի', 'հունիսի', 'հուլիսի', 'օգոստոսի', 'սեպտեմբերի', 'հոկտեմբերի', 'նոյեմբերի', 'դեկտեմբերի']

/** '2027-06-30' → '30 июня 2027 г.': for validity dates, where the year matters */
export function fullDate(iso: string, lang: Lang): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  if (lang === 'hy') return `${d} ${HY_MONTHS[m - 1]} ${y} թ.`
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(LOCALES[lang], { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
}

/** "2026-10-23" -> "Fri, 23 Oct" in the user's language (Yerevan calendar day as sent by the API). */
export function date(iso: string, lang: Lang): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(LOCALES[lang], {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  })
}

/** "10:00:00", "11:00:00" -> "10:00–11:00" */
export function slot(from: string, to: string): string {
  return `${from.slice(0, 5)}–${to.slice(0, 5)}`
}

/** Timestamp in Yerevan time: "23 Oct, 14:05". */
export function dateTime(iso: string, lang: Lang): string {
  return new Date(iso).toLocaleString(LOCALES[lang], {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Yerevan',
  })
}
