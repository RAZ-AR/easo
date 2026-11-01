import { expect, type APIRequestContext, type Page } from '@playwright/test'

/** Demo users from backend/app/seed.py; the stack must run with DEV_LOGIN. */
export const USERS = {
  arax: 'arax@easo.local', // Cafe Arax, buyer, Lori's own client
  nova: 'nova@easo.local', // Bar Nova, buyer (cannot order until its register extract is checked)
  lori: 'lori@easo.local', // Լոռի Պանիր, seller
  admin: 'admin@easo.local',
} as const

export const CHEESE = 'Lori cheese' // 3 500 ֏/kg from Lori

export type Session = { token: string; headers: Record<string, string> }

/** Signs in through the API, accepts pending consents, returns the bearer token. */
export async function apiLogin(request: APIRequestContext, email: string): Promise<Session> {
  const r = await request.post('/api/auth/dev-login', { data: { email } })
  expect(r.ok(), await r.text()).toBeTruthy()
  const { access_token: token } = (await r.json()) as { access_token: string }
  const headers = { Authorization: `Bearer ${token}` }
  await request.post('/api/auth/consent', { headers }) // a no-op when nothing is pending
  return { token, headers }
}

/** English UI for readable assertions; optionally signed in as a demo user before the first page load. */
export async function open(page: Page, path = '/', token?: string) {
  await page.addInitScript((t) => {
    if (sessionStorage.getItem('e2e.init')) return // only on the first load, so sign-out sticks
    sessionStorage.setItem('e2e.init', '1')
    localStorage.setItem('easo.lang', 'en')
    if (t) localStorage.setItem('easo.token', t)
    localStorage.setItem('easo.haggle.howto.seen', '1')
  }, token ?? null)
  await page.goto(path)
}

/** Empties a user's server-side cart so each test starts clean. */
export async function clearCart(request: APIRequestContext, s: Session) {
  const cart = (await (await request.get('/api/cart', { headers: s.headers })).json()) as {
    groups: { lines: { product_id: string }[] }[]
  }
  for (const g of cart.groups) {
    for (const l of g.lines) await request.delete(`/api/cart/items/${l.product_id}`, { headers: s.headers })
  }
}

/** Puts a product into the cart through the API, optionally with a price offer. */
export async function putInCart(request: APIRequestContext, s: Session, nameEn: string, qty: string, offer: number | null = null) {
  const id = await productId(request, s, nameEn)
  const r = await request.put(`/api/cart/items/${id}`, { headers: s.headers, data: { qty, offer_price_amd: offer } })
  expect(r.ok(), await r.text()).toBeTruthy()
}

export async function productId(request: APIRequestContext, s: Session, nameEn: string): Promise<string> {
  const r = await request.get('/api/products', { headers: s.headers, params: { q: nameEn, limit: 100 } })
  const page = (await r.json()) as { items: { id: string; name: { en?: string } }[] }
  const p = page.items.find((x) => x.name.en === nameEn)
  if (!p) throw new Error(`no product ${nameEn}: run python -m app.seed`)
  return p.id
}
