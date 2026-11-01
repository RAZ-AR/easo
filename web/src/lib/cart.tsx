import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type Cart } from './api'

/**
 * One cart interface, two stores:
 * - guests (and users without a buying company yet) keep the cart in the browser;
 * - a buying company keeps it on the server. The browser cart moves there once, on the first sign-in.
 */
interface CartState {
  cart: Cart | null // server cart (null for guests)
  local: Record<string, string> // guest cart: product id -> qty
  guest: boolean
  count: number
  qtyOf: (productId: string) => string | null
  setItem: (productId: string, qty: string, offer?: number | null) => Promise<void>
  remove: (productId: string) => Promise<void>
  refresh: () => Promise<void>
  error: string | null
}

const KEY = 'easo.guestCart'
const CartContext = createContext<CartState | null>(null)

function readLocal(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, string>
  } catch {
    return {}
  }
}

function writeLocal(items: Record<string, string>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(items))
  } catch {
    /* private mode: the guest cart lives only in memory */
  }
}

export function CartProvider({ server, children }: { server: boolean; children: ReactNode }) {
  const [cart, setCart] = useState<Cart | null>(null)
  const [local, setLocal] = useState<Record<string, string>>(readLocal)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async (p: Promise<Cart>) => {
    try {
      setCart(await p)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
      throw e
    }
  }, [])

  const refresh = useCallback(async () => {
    if (server) await run(api.cart())
  }, [server, run])

  // First time with a buying company: move the guest cart to the server
  useEffect(() => {
    if (!server) return
    const items = readLocal()
    void (async () => {
      for (const [id, qty] of Object.entries(items)) {
        try {
          await api.setCartItem(id, qty)
        } catch {
          /* product gone or own product: skip it */
        }
      }
      if (Object.keys(items).length) {
        writeLocal({})
        setLocal({})
      }
      await refresh().catch(() => {})
    })()
  }, [server, refresh])

  const value = useMemo<CartState>(() => {
    if (!server) {
      const update = (next: Record<string, string>) => {
        writeLocal(next)
        setLocal(next)
      }
      return {
        cart: null,
        local,
        guest: true,
        error: null,
        count: Object.keys(local).length,
        qtyOf: (id) => local[id] ?? null,
        setItem: async (id, qty) => update({ ...local, [id]: qty }),
        remove: async (id) => {
          const next = { ...local }
          delete next[id]
          update(next)
        },
        refresh: async () => {},
      }
    }
    const lines = cart?.groups.flatMap((g) => g.lines) ?? []
    return {
      cart,
      local: {},
      guest: false,
      error,
      count: lines.length,
      qtyOf: (id) => lines.find((l) => l.product_id === id)?.qty ?? null,
      setItem: (id, qty, offer = null) => run(api.setCartItem(id, qty, offer)),
      remove: (id) => run(api.removeCartItem(id)),
      refresh,
    }
  }, [server, local, cart, error, run, refresh])

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>
}

export function useCart(): CartState {
  const ctx = useContext(CartContext)
  if (!ctx) throw new Error('useCart outside CartProvider')
  return ctx
}

/** Cart actions when available; null outside a provider. */
export function useCartMaybe(): CartState | null {
  return useContext(CartContext)
}
