import { useState, useEffect } from 'react'
import { hiringAPI } from '../lib/api'
import type { HiringPosition, AgentNomination } from '../lib/types'

interface ReviewNominationsModalProps {
  position: HiringPosition
  onClose: () => void
  onSuccess: () => void
}

export function ReviewNominationsModal({ position, onClose, onSuccess }: ReviewNominationsModalProps) {
  const [nominations, setNominations] = useState<AgentNomination[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [reviewingId, setReviewingId] = useState<number | null>(null)
  const [reviewNotes, setReviewNotes] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    loadNominations()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position.id])

  const loadNominations = async () => {
    setIsLoading(true)
    try {
      const positionData = await hiringAPI.getPosition(position.id)
      setNominations(positionData.nominations || [])
    } catch (error) {
      console.error('Failed to load nominations:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleReview = async (nominationId: number, status: 'approved' | 'rejected') => {
    setReviewingId(nominationId)
    setError('')
    try {
      await hiringAPI.reviewNomination(nominationId, {
        status,
        review_notes: reviewNotes || undefined,
      })
      setReviewNotes('')
      await loadNominations()
      onSuccess()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to review nomination')
    } finally {
      setReviewingId(null)
    }
  }

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50">
        <div className="bg-dark-100/95 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
          <div className="flex items-center gap-3">
            <div className="animate-spin rounded-full h-6 w-6 border-3 border-primary-400 border-t-transparent"></div>
            <p className="text-white/60 font-semibold">Loading nominations...</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-dark-100/95 backdrop-blur-xl rounded-2xl shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-y-auto border border-dark-200/50">
        <div className="p-8">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-4xl font-black text-white tracking-tight">Review Nominations</h2>
            <button
              onClick={onClose}
              className="text-white/60 hover:text-white text-3xl transition-colors"
            >
              ×
            </button>
          </div>

          <div className="mb-6 p-4 bg-dark-200/30 rounded-xl border border-dark-300">
            <h3 className="font-black text-white text-xl">{position.title}</h3>
          </div>

          {error && (
            <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
              {error}
            </div>
          )}

          {nominations.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-white/60 text-lg font-semibold">No nominations yet for this position</p>
            </div>
          ) : (
            <div className="space-y-4">
              {nominations.map((nomination) => (
                <div
                  key={nomination.id}
                  className={`border-2 rounded-2xl p-6 ${
                    nomination.status === 'approved'
                      ? 'border-green-500/50 bg-green-500/10'
                      : nomination.status === 'rejected'
                      ? 'border-red-500/50 bg-red-500/10'
                      : 'border-dark-300 bg-dark-200/30'
                  }`}
                >
                  <div className="flex justify-between items-start mb-4">
                    <div>
                      <h4 className="font-black text-white text-xl mb-2">{nomination.agent_name}</h4>
                      <p className="text-sm text-white/60 font-medium">Developer: {nomination.developer_email}</p>
                      {nomination.cover_letter && (
                        <div className="mt-4">
                          <p className="text-sm font-bold text-white mb-2">Cover Letter:</p>
                          <p className="text-sm text-white/80 bg-dark-200/50 p-4 rounded-xl mt-2 font-medium leading-relaxed">
                            {nomination.cover_letter}
                          </p>
                        </div>
                      )}
                    </div>
                    <span className={`px-4 py-2 rounded-full text-xs font-bold border ${
                      nomination.status === 'approved'
                        ? 'bg-green-500/20 text-green-400 border-green-500/50'
                        : nomination.status === 'rejected'
                        ? 'bg-red-500/20 text-red-400 border-red-500/50'
                        : 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50'
                    }`}>
                      {nomination.status.toUpperCase()}
                    </span>
                  </div>

                  {nomination.review_notes && (
                    <div className="mt-4 text-sm">
                      <p className="font-bold text-white mb-2">Review Notes:</p>
                      <p className="text-white/80 font-medium">{nomination.review_notes}</p>
                    </div>
                  )}

                  {nomination.status === 'pending' && (
                    <div className="mt-6 pt-6 border-t border-dark-300">
                      <textarea
                        value={reviewNotes}
                        onChange={(e) => setReviewNotes(e.target.value)}
                        placeholder="Add review notes (optional)..."
                        rows={3}
                        className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 mb-4 font-medium resize-none"
                      />
                      <div className="flex gap-3">
                        <button
                          onClick={() => handleReview(nomination.id, 'approved')}
                          disabled={reviewingId === nomination.id}
                          className="bg-gradient-to-r from-green-500 to-green-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-green-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
                        >
                          {reviewingId === nomination.id ? (
                            <span className="flex items-center gap-2">
                              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                              Processing...
                            </span>
                          ) : (
                            'Approve & Push to Marketplace'
                          )}
                        </button>
                        <button
                          onClick={() => handleReview(nomination.id, 'rejected')}
                          disabled={reviewingId === nomination.id}
                          className="bg-gradient-to-r from-red-500 to-red-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-red-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
                        >
                          {reviewingId === nomination.id ? (
                            <span className="flex items-center gap-2">
                              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                              Processing...
                            </span>
                          ) : (
                            'Reject'
                          )}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="mt-8 flex justify-end">
            <button
              onClick={onClose}
              className="bg-dark-200/50 text-white/80 hover:text-white px-6 py-3 rounded-xl font-bold hover:bg-dark-200 border border-dark-300 transition-all duration-200"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
