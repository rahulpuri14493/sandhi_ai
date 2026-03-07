import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { agentsAPI } from '../lib/api'
import { useAuthStore } from '../lib/store'
import type { Agent, AgentReviewSummary, AgentReview } from '../lib/types'

const STARS = [1, 2, 3, 4, 5]
const REVIEW_TEXT_MAX = 2000

function StarRating({ value, size = 'md' }: { value: number; size?: 'sm' | 'md' }) {
  const scale = size === 'sm' ? 'w-4 h-4' : 'w-6 h-6'
  return (
    <span className="inline-flex items-center gap-0.5" role="img" aria-label={`Rating: ${value} out of 5`}>
      {STARS.map((star) => (
        <span
          key={star}
          className={`${scale} ${value >= star ? 'text-amber-400' : 'text-dark-300'}`}
          aria-hidden
        >
          ★
        </span>
      ))}
    </span>
  )
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return iso
  }
}

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const agentId = parseInt(id || '0')
  const { user } = useAuthStore()
  const [agent, setAgent] = useState<Agent | null>(null)
  const [summary, setSummary] = useState<AgentReviewSummary | null>(null)
  const [reviews, setReviews] = useState<AgentReview[]>([])
  const [reviewsTotal, setReviewsTotal] = useState(0)
  const [editingReviewId, setEditingReviewId] = useState<number | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [reviewsLoading, setReviewsLoading] = useState(false)
  const [formRating, setFormRating] = useState(5)
  const [formText, setFormText] = useState('')
  const [formSubmitting, setFormSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const resetForm = () => {
    setFormRating(5)
    setFormText('')
    setEditingReviewId(null)
  }

  const loadAgent = async () => {
    try {
      const data = await agentsAPI.get(agentId)
      setAgent(data)
    } catch (error) {
      console.error('Failed to load agent:', error)
      setAgent(null)
    } finally {
      setIsLoading(false)
    }
  }

  const loadSummaryAndReviews = async () => {
    setReviewsLoading(true)
    try {
      const [summaryRes, listRes] = await Promise.all([
        agentsAPI.getReviewSummary(agentId),
        agentsAPI.listReviews(agentId, 20, 0),
      ])
      setSummary(summaryRes)
      setReviews(listRes.items)
      setReviewsTotal(listRes.total)
    } catch (error) {
      console.error('Failed to load reviews:', error)
      setSummary(null)
      setReviews([])
      setReviewsTotal(0)
    } finally {
      setReviewsLoading(false)
    }
  }

  useEffect(() => {
    if (agentId) {
      loadAgent()
    }
  }, [agentId])

  useEffect(() => {
    if (agentId && agent) {
      loadSummaryAndReviews()
    }
  }, [agentId, agent?.id])

  const handleSubmitReview = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!user) return
    setFormError(null)
    setFormSubmitting(true)
    try {
      if (editingReviewId !== null) {
        await agentsAPI.updateReview(agentId, editingReviewId, {
          rating: formRating,
          review_text: formText.trim() || undefined,
        })
        resetForm()
      } else {
        await agentsAPI.submitReview(agentId, formRating, formText.trim() || undefined)
        resetForm()
      }
      await loadSummaryAndReviews()
    } catch (err: any) {
      setFormError(err.response?.data?.detail || 'Failed to save review')
    } finally {
      setFormSubmitting(false)
    }
  }

  const startEditReview = (r: AgentReview) => {
    if (!r.is_own) return
    setEditingReviewId(r.id)
    setFormRating(r.rating)
    setFormText(r.review_text || '')
    setFormError(null)
  }

  const handleDeleteReview = async (reviewId: number) => {
    if (!user) return
    if (!window.confirm('Remove this review?')) return
    setFormError(null)
    try {
      await agentsAPI.deleteReview(agentId, reviewId)
      if (editingReviewId === reviewId) resetForm()
      await loadSummaryAndReviews()
    } catch (err: any) {
      setFormError(err.response?.data?.detail || 'Failed to delete review')
    }
  }

  if (isLoading) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
            <p className="text-white/60 text-lg font-semibold">Loading agent...</p>
          </div>
        </div>
      </div>
    )
  }

  if (!agent) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="text-center py-16">
          <p className="text-white/60 text-xl font-semibold">Agent not found</p>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="max-w-4xl mx-auto">
        <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
          <h1 className="text-6xl font-black text-white tracking-tight mb-6">{agent.name}</h1>
          <p className="text-white/70 text-xl mb-8 font-medium leading-relaxed">{agent.description}</p>

          <div className="grid md:grid-cols-2 gap-6 mb-8">
            <div className="p-6 bg-dark-200/30 rounded-xl border border-dark-300">
              <h3 className="font-bold text-white mb-3 text-lg">Pricing</h3>
              {agent.pricing_model === 'monthly' && agent.monthly_price ? (
                <p className="text-3xl font-black text-primary-400">
                  ${agent.monthly_price.toFixed(2)} <span className="text-base font-medium text-white/50">/month</span>
                </p>
              ) : agent.pricing_model === 'quarterly' && agent.quarterly_price ? (
                <p className="text-3xl font-black text-primary-400">
                  ${agent.quarterly_price.toFixed(2)} <span className="text-base font-medium text-white/50">/quarter</span>
                </p>
              ) : (
                <>
                  <p className="text-3xl font-black text-primary-400">
                    ${agent.price_per_task.toFixed(2)} <span className="text-base font-medium text-white/50">per task</span>
                  </p>
                  <p className="text-sm text-white/50 mt-2 font-medium">
                    ${agent.price_per_communication.toFixed(2)} per communication
                  </p>
                </>
              )}
            </div>
            <div className="p-6 bg-dark-200/30 rounded-xl border border-dark-300">
              <h3 className="font-bold text-white mb-3 text-lg">Status</h3>
              <span className={`px-4 py-2 rounded-full text-sm font-bold border ${
                agent.status === 'active'
                  ? 'bg-green-500/20 text-green-400 border-green-500/50'
                  : 'bg-dark-200/50 text-white/60 border-dark-300'
              }`}>
                {agent.status.toUpperCase()}
              </span>
            </div>
          </div>

          {/* Overall average rating */}
          <div className="mb-8 p-6 bg-dark-200/30 rounded-xl border border-dark-300">
            <h3 className="font-bold text-white mb-3 text-lg">Overall rating</h3>
            {reviewsLoading ? (
              <p className="text-white/50">Loading...</p>
            ) : summary ? (
              <div className="flex flex-wrap items-center gap-4">
                <StarRating value={Math.round(summary.average_rating * 2) / 2} size="md" />
                <span className="text-2xl font-bold text-white">
                  {summary.average_rating.toFixed(1)}
                </span>
                <span className="text-white/60">
                  ({summary.total_count} {summary.total_count === 1 ? 'review' : 'reviews'})
                </span>
              </div>
            ) : (
              <p className="text-white/50">No reviews yet.</p>
            )}
          </div>

          {agent.capabilities && agent.capabilities.length > 0 && (
            <div className="mb-8">
              <h3 className="font-black text-white mb-4 text-xl">Capabilities</h3>
              <div className="flex flex-wrap gap-3">
                {agent.capabilities.map((cap, idx) => (
                  <span
                    key={idx}
                    className="px-4 py-2 bg-primary-500/20 text-primary-400 rounded-xl border border-primary-500/30 font-semibold"
                  >
                    {cap}
                  </span>
                ))}
              </div>
            </div>
          )}

          {user && agent.api_endpoint && (
            <div className="mb-8">
              <h3 className="font-black text-white mb-3 text-xl">API Endpoint</h3>
              <code className="bg-dark-200/50 px-4 py-3 rounded-xl text-sm text-white/80 font-mono border border-dark-300 block">
                {agent.api_endpoint}
              </code>
            </div>
          )}

          {/* Review form (logged-in only). By default shows "Rate this agent"; no pre-filled update. */}
          {user && (
            <div className="mb-10 p-6 bg-dark-200/30 rounded-xl border border-dark-300">
              <h3 className="font-bold text-white mb-4 text-lg">
                {editingReviewId !== null ? 'Update your review' : 'Rate this agent'}
              </h3>
              <form onSubmit={handleSubmitReview} className="space-y-4">
                <div>
                  <label className="block text-white/80 text-sm font-medium mb-2">Rating</label>
                  <div className="flex gap-2">
                    {STARS.map((star) => (
                      <button
                        key={star}
                        type="button"
                        onClick={() => setFormRating(star)}
                        className={`p-1 rounded text-2xl transition-colors ${
                          formRating >= star ? 'text-amber-400' : 'text-dark-400 hover:text-white/60'
                        }`}
                        aria-label={`${star} star${star > 1 ? 's' : ''}`}
                      >
                        ★
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-white/80 text-sm font-medium mb-2">Review (optional)</label>
                  <textarea
                    value={formText}
                    onChange={(e) => setFormText(e.target.value.slice(0, REVIEW_TEXT_MAX))}
                    placeholder="Share your experience with this agent..."
                    rows={3}
                    className="w-full bg-dark-100 border border-dark-300 rounded-xl px-4 py-3 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500/50 focus:border-primary-500"
                  />
                  <p className="text-white/40 text-xs mt-1">{formText.length} / {REVIEW_TEXT_MAX}</p>
                </div>
                {formError && (
                  <p className="text-red-400 text-sm">{formError}</p>
                )}
                <div className="flex flex-wrap gap-3">
                  <button
                    type="submit"
                    disabled={formSubmitting}
                    className="px-5 py-2.5 bg-primary-500 hover:bg-primary-400 text-white font-semibold rounded-xl disabled:opacity-50"
                  >
                    {formSubmitting ? 'Saving...' : editingReviewId !== null ? 'Update review' : 'Submit review'}
                  </button>
                  {editingReviewId !== null && (
                    <button
                      type="button"
                      onClick={() => resetForm()}
                      disabled={formSubmitting}
                      className="px-5 py-2.5 bg-dark-200 hover:bg-dark-300 text-white/80 font-semibold rounded-xl border border-dark-300 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </form>
            </div>
          )}

          {/* List of reviews */}
          <div>
            <h3 className="font-black text-white mb-4 text-xl">User ratings and reviews</h3>
            {reviewsLoading ? (
              <p className="text-white/50">Loading...</p>
            ) : reviews.length === 0 ? (
              <p className="text-white/50">No reviews yet. Be the first to rate this agent.</p>
            ) : (
              <ul className="space-y-4">
                {reviews.map((r) => (
                  <li
                    key={r.id}
                    className="p-4 bg-dark-200/30 rounded-xl border border-dark-300"
                  >
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <StarRating value={r.rating} size="sm" />
                      {r.is_own && (
                        <span className="px-2 py-0.5 rounded text-xs font-semibold bg-primary-500/20 text-primary-400 border border-primary-500/30">
                          Your review
                        </span>
                      )}
                      <span className="text-white/40 text-sm">{formatDate(r.created_at)}</span>
                      {r.is_own && (
                        <span className="ml-auto flex gap-2">
                          <button
                            type="button"
                            onClick={() => startEditReview(r)}
                            className="text-sm text-primary-400 hover:text-primary-300 font-medium"
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteReview(r.id)}
                            className="text-sm text-red-400 hover:text-red-300 font-medium"
                          >
                            Delete
                          </button>
                        </span>
                      )}
                    </div>
                    {r.review_text && (
                      <p className="text-white/80 font-medium whitespace-pre-wrap">{r.review_text}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
