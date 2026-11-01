/**
 * The supplier link a visitor came through. Kept across sign-in and sign-up, so the new company becomes that
 * supplier's client (0% commission) once it is registered.
 */
const KEY = 'easo.invite'

export const pendingInvite = {
  get(): string | null {
    try {
      return localStorage.getItem(KEY)
    } catch {
      return null
    }
  },
  set(code: string) {
    try {
      localStorage.setItem(KEY, code)
    } catch {
      /* storage blocked: the page still works, the link is just not remembered */
    }
  },
  clear() {
    try {
      localStorage.removeItem(KEY)
    } catch {
      /* ignore */
    }
  },
}
