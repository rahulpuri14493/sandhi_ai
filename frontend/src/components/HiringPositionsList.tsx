import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { hiringAPI } from '../lib/api'
import type { HiringPosition } from '../lib/types'
import { useAuthStore } from '../lib/store'
import { NominateAgentModal } from './NominateAgentModal'
import { ReviewNominationsModal } from './ReviewNominationsModal'

export function HiringPositionsList() {
  const { user } = useAuthStore()
  const [positions, setPositions] = useState<HiringPosition[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [selectedPosition, setSelectedPosition] = useState<HiringPosition | null>(null)
  const [showNominateModal, setShowNominateModal] = useState(false)
  const [showReviewModal, setShowReviewModal] = useState(false)

  useEffect(() => {
    loadPositions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadPositions = async () => {
    setIsLoading(true)
    try {
      const data = await hiringAPI.listPositions('open')
      setPositions(data)
    } catch (error) {
      console.error('Failed to load hiring positions:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleNominate = (position: HiringPosition) => {
    setSelectedPosition(position)
    setShowNominateModal(true)
  }

  const handleReview = (position: HiringPosition) => {
    setSelectedPosition(position)
    setShowReviewModal(true)
  }

  if (isLoading) {
    return (
      <div className="text-center py-16">
        <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
        <p className="text-white/60 text-lg font-semibold">Loading open positions...</p>
      </div>
    )
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-10">
        <h2 className="text-5xl font-black text-white tracking-tight">Open Positions for AI Agents</h2>
        {user?.role === 'business' && (
          <Link
            to="/hirings/new"
            className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
          >
            Post New Position
          </Link>
        )}
      </div>

      {positions.length === 0 ? (
        <div className="text-center py-16">
          <p className="text-white/60 text-xl font-semibold mb-2">No open positions available</p>
          <p className="text-white/40 text-sm font-medium">Check back later for new opportunities</p>
        </div>
      ) : (
        <div className="space-y-6">
          {positions.map((position) => (
            <div key={position.id} className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50 hover:border-primary-500/50 transition-all duration-200">
              <div className="flex justify-between items-start mb-6">
                <div className="flex-1">
                  <h3 className="text-3xl font-black text-white mb-3">{position.title}</h3>
                  {position.description && (
                    <p className="text-white/70 text-lg mb-4 font-medium leading-relaxed">{position.description}</p>
                  )}
                  {position.requirements && (
                    <div className="mt-6">
                      <h4 className="font-black text-white mb-3 text-xl">Roles & Responsibilities:</h4>
                      <div className="bg-dark-200/50 p-6 rounded-xl border border-dark-300">
                        <p className="text-white/80 whitespace-pre-line font-medium leading-relaxed">{position.requirements}</p>
                      </div>
                    </div>
                  )}
                </div>
                <span className={`px-4 py-2 rounded-full text-sm font-bold border ${
                  position.status === 'open'
                    ? 'bg-green-500/20 text-green-400 border-green-500/50'
                    : position.status === 'closed'
                    ? 'bg-dark-200/50 text-white/60 border-dark-300'
                    : 'bg-blue-500/20 text-blue-400 border-blue-500/50'
                }`}>
                  {position.status.toUpperCase()}
                </span>
              </div>
              
              <div className="flex justify-between items-center mt-6 pt-6 border-t border-dark-200/50">
                <div className="text-sm text-white/50 font-medium">
                  {position.nomination_count || 0} nomination{position.nomination_count !== 1 ? 's' : ''}
                  {' • '}
                  Posted {new Date(position.created_at).toLocaleDateString()}
                </div>
                <div className="flex gap-3">
                  {user?.role === 'developer' && position.status === 'open' && (
                    <button
                      onClick={() => handleNominate(position)}
                      className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
                    >
                      Nominate Agent
                    </button>
                  )}
                  {user?.role === 'business' && position.business_id === user.id && (
                    <button
                      onClick={() => handleReview(position)}
                      className="bg-gradient-to-r from-blue-500 to-blue-700 text-white px-6 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-blue-500/50 hover:scale-105 transition-all duration-200"
                    >
                      Review Nominations ({position.nomination_count || 0})
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showNominateModal && selectedPosition && (
        <NominateAgentModal
          position={selectedPosition}
          onClose={() => {
            setShowNominateModal(false)
            setSelectedPosition(null)
          }}
          onSuccess={() => {
            loadPositions()
            setShowNominateModal(false)
            setSelectedPosition(null)
          }}
        />
      )}

      {showReviewModal && selectedPosition && (
        <ReviewNominationsModal
          position={selectedPosition}
          onClose={() => {
            setShowReviewModal(false)
            setSelectedPosition(null)
          }}
          onSuccess={() => {
            loadPositions()
            setShowReviewModal(false)
            setSelectedPosition(null)
          }}
        />
      )}
    </div>
  )
}
