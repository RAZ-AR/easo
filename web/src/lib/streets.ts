// Yerevan street suggestions from our own list (scripts/build_yerevan_streets.py, © OpenStreetMap, ODbL).
// No network while typing: Nominatim forbids as-you-type use, so only the finished address goes to it.
import type { Lang } from './api'

export interface Street {
  hy: string
  ru?: string
  en?: string
  d: string[] // Easo districts the street runs through
}

let cache: Promise<Street[]> | null = null

/** The list is ~160 KB: loaded on first use of an address field, then kept. */
export function loadStreets(): Promise<Street[]> {
  cache ??= import('../data/yerevan-streets.json').then((m) => m.default as Street[])
  return cache
}

// Street-type words that people type or skip at will
const NOISE = new Set([
  'улица', 'ул', 'проспект', 'пр-т', 'пр', 'переулок', 'пер', 'тупик', 'шоссе', 'площадь', 'пл',
  'street', 'st', 'avenue', 'ave', 'lane', 'square', 'road', 'highway', 'alley', 'blind', 'deadlock',
  'փողոց', 'փ', 'պողոտա', 'պող', 'նրբանցք', 'փակուղի', 'խճուղի', 'հրապարակ',
])

/** Lower case, no quotes or dots, no street-type words: what is left to compare. */
export function core(s: string): string {
  return s
    .toLowerCase()
    .replace(/[«»"'.,]/g, ' ')
    .split(/\s+/)
    .filter((w) => w && !NOISE.has(w))
    .join(' ')
}

export function streetName(s: Street, lang: Lang): string {
  return (lang === 'ru' ? s.ru : lang === 'en' ? s.en : undefined) ?? s.hy
}

/** "Абовяна 10" → { street: "Абовяна", house: "10" }; house is null until a number is typed. */
export function splitHouse(text: string): { street: string; house: string | null } {
  const m = text.trim().match(/^(.*?)[\s,]+(\d+[\p{L}\d/-]*)$/u)
  return m ? { street: m[1].replace(/,\s*$/, ''), house: m[2] } : { street: text.trim(), house: null }
}

/** Best matches for what is typed: whole-word starts beat substrings, shorter names win ties. */
export function suggest(streets: Street[], typed: string, limit = 6): Street[] {
  const q = core(splitHouse(typed).street)
  if (q.length < 2) return []
  const scored: [number, Street][] = []
  for (const s of streets) {
    let best = 0
    for (const name of [s.hy, s.ru, s.en]) {
      if (!name) continue
      const c = core(name)
      const score = c.startsWith(q) ? 3 : c.split(' ').some((w) => w.startsWith(q)) ? 2 : c.includes(q) ? 1 : 0
      best = Math.max(best, score)
    }
    if (best) scored.push([best * 1000 - s.hy.length, s])
  }
  return scored.sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, s]) => s)
}
