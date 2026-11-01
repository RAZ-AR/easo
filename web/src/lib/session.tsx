import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, context, token, type Me, type Mode, type Org } from './api'

interface SessionState {
  me: Me | null
  org: Org | null
  role: Me['memberships'][number]['role'] | null
  mode: Mode
  setMode: (m: Mode) => void
  buyerReady: boolean
  canSell: boolean
  reload: () => Promise<void>
  signIn: (accessToken: string) => Promise<void>
  signOut: () => void
}

const SessionContext = createContext<SessionState | null>(null)
const modeKey = (orgId: string) => `easo.mode.${orgId}`

function storedMode(org: Org | null): Mode {
  if (!org) return 'buyer'
  try {
    const m = localStorage.getItem(modeKey(org.id))
    if (m === 'seller' && org.is_seller) return 'seller'
    if (m === 'buyer' && org.is_buyer) return 'buyer'
  } catch {
    /* storage blocked: fall back below */
  }
  return org.is_buyer ? 'buyer' : 'seller'
}

/** Who is signed in, for which company, on which side. Feeds the X-Org-Id / X-Mode headers of the API client. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [checked, setChecked] = useState(false)
  const [mode, setModeState] = useState<Mode>('buyer')

  const membership = me?.memberships[0] ?? null
  const org = membership?.organization ?? null

  const reload = useCallback(async () => {
    if (!token.get()) {
      setMe(null)
      setChecked(true)
      return
    }
    try {
      const m = await api.me()
      setMe(m)
      const o = m.memberships[0]?.organization ?? null
      context.orgId = o?.id ?? null
      const md = storedMode(o)
      context.mode = o ? md : null
      setModeState(md)
    } catch {
      setMe(null)
    } finally {
      setChecked(true)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const setMode = useCallback(
    (m: Mode) => {
      context.mode = m
      setModeState(m)
      try {
        if (org) localStorage.setItem(modeKey(org.id), m)
      } catch {
        /* not remembered */
      }
    },
    [org],
  )

  const value = useMemo<SessionState>(
    () => ({
      me,
      org,
      role: membership?.role ?? null,
      mode,
      setMode,
      buyerReady: membership?.buyer_ready ?? false,
      canSell: !!org && org.is_seller && org.verification_status === 'verified' && org.status !== 'blocked',
      reload,
      signIn: async (accessToken: string) => {
        token.set(accessToken)
        await reload()
      },
      signOut: () => {
        token.clear()
        context.orgId = null
        context.mode = null
        setMe(null)
      },
    }),
    [me, org, membership, mode, setMode, reload],
  )

  if (!checked) return null
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionState {
  const s = useContext(SessionContext)
  if (!s) throw new Error('useSession outside SessionProvider')
  return s
}
