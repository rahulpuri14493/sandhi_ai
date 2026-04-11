import { useEffect } from 'react'

interface FlashToastProps {
  message: string | null
  onDismiss: () => void
  durationMs?: number
}

export function FlashToast({ message, onDismiss, durationMs = 6000 }: FlashToastProps) {
  useEffect(() => {
    if (!message) return
    const t = window.setTimeout(onDismiss, durationMs)
    return () => window.clearTimeout(t)
  }, [message, onDismiss, durationMs])

  if (!message) return null

  return (
    <div
      className="fixed bottom-6 left-1/2 z-[100] -translate-x-1/2 px-5 py-3 rounded-xl border border-primary-500/40 bg-dark-100/95 backdrop-blur-md text-white shadow-2xl shadow-black/50 max-w-lg w-[calc(100%-2rem)] flex items-start gap-3"
      role="status"
    >
      <p className="text-sm font-medium text-white/90 flex-1 leading-relaxed">{message}</p>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 text-white/60 hover:text-white font-bold text-lg leading-none px-1"
        aria-label="Dismiss notification"
      >
        ×
      </button>
    </div>
  )
}
