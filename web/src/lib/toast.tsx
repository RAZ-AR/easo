import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const ToastContext = createContext<(text: string) => void>(() => {})
const SHOW_MS = 2600

/** One short confirmation above the dock ("Order cancelled"); a new one replaces the old. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const show = useCallback((text: string) => {
    clearTimeout(timer.current)
    setToast({ id: Date.now(), text })
    timer.current = setTimeout(() => setToast(null), SHOW_MS)
  }, [])
  return (
    <ToastContext.Provider value={show}>
      {children}
      {createPortal(
        <div className="toast-slot" role="status" aria-live="polite">
          {toast && (
            <div key={toast.id} className="toast">
              {toast.text}
            </div>
          )}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

export function useToast() {
  return useContext(ToastContext)
}
