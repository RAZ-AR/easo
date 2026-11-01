// The service was renamed from QB to Easo: carry saved keys (sign-in, cart, language…) over once,
// so nobody is signed out. Imported first in main.tsx, before anything reads localStorage.
try {
  for (const key of Object.keys(localStorage)) {
    if (!key.startsWith('qb.')) continue
    const next = 'easo.' + key.slice(3)
    const value = localStorage.getItem(key)
    if (value !== null && localStorage.getItem(next) === null) localStorage.setItem(next, value)
    localStorage.removeItem(key)
  }
} catch {
  // storage unavailable (private mode): nothing to carry over
}

export {}
