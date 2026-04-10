type RerunMode = 'resume' | 'full'

interface RerunModeModalProps {
  isOpen: boolean
  isSubmitting?: boolean
  onClose: () => void
  onSelect: (mode: RerunMode) => void
}

export function RerunModeModal({ isOpen, isSubmitting = false, onClose, onSelect }: RerunModeModalProps) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-dark-100/95 backdrop-blur-xl rounded-2xl shadow-2xl max-w-lg w-full border border-dark-200/50">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-2xl font-black text-white">Choose Rerun Mode</h3>
            <button
              onClick={onClose}
              disabled={isSubmitting}
              className="text-white/60 hover:text-white text-2xl transition-colors disabled:opacity-50"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <p className="text-sm text-white/70 mb-5">
            Pick how you want to run this job again.
          </p>
          <div className="space-y-3">
            <button
              onClick={() => onSelect('resume')}
              disabled={isSubmitting}
              className="w-full text-left p-4 rounded-xl border border-primary-500/50 bg-primary-500/15 hover:bg-primary-500/25 transition-all disabled:opacity-50"
            >
              <div className="text-white font-bold">Resume (recommended)</div>
              <div className="text-xs text-white/70 mt-1">
                Reuse completed steps and rerun only incomplete/failed steps.
              </div>
            </button>
            <button
              onClick={() => onSelect('full')}
              disabled={isSubmitting}
              className="w-full text-left p-4 rounded-xl border border-orange-500/50 bg-orange-500/10 hover:bg-orange-500/20 transition-all disabled:opacity-50"
            >
              <div className="text-white font-bold">Full rerun</div>
              <div className="text-xs text-white/70 mt-1">
                Reset and rerun all workflow steps from the beginning.
              </div>
            </button>
          </div>
          <div className="mt-5 flex justify-end">
            <button
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 rounded-xl border border-dark-300 bg-dark-200/50 text-white/80 hover:text-white transition-all disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
