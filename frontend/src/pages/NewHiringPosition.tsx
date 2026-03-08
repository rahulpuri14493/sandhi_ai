import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { hiringAPI } from '../lib/api'
import type { HiringPosition } from '../lib/types'

export default function NewHiringPositionPage() {
  const navigate = useNavigate()
  const [formData, setFormData] = useState<Partial<HiringPosition>>({
    title: '',
    description: '',
    requirements: '',
  })
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError('')

    try {
      await hiringAPI.createPosition({
        title: formData.title ?? '',
        description: formData.description,
        requirements: formData.requirements,
      })
      navigate('/')
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create position')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="max-w-3xl mx-auto">
        <div className="mb-10">
          <button
            onClick={() => navigate(-1)}
            className="flex items-center text-white/70 hover:text-white transition-all duration-200 mb-6 px-4 py-2.5 rounded-xl hover:bg-dark-200/50"
          >
            <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            <span className="font-semibold">Back</span>
          </button>
          <h1 className="text-6xl font-black text-white tracking-tight mb-4">Post New Hiring Position</h1>
          <p className="text-white/60 text-xl font-medium">
            Create a new position for AI agents. Developers will be able to nominate their agents for this role.
          </p>
        </div>

        {error && (
          <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="title">
              Position Title *
            </label>
            <input
              id="title"
              type="text"
              value={formData.title}
              onChange={(e) => setFormData({ ...formData, title: e.target.value })}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              placeholder="Enter position title..."
              required
            />
          </div>

          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="description">
              Description
            </label>
            <textarea
              id="description"
              value={formData.description || ''}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              rows={5}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium resize-none"
              placeholder="Describe the position and what you're looking for..."
            />
          </div>

          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="requirements">
              Roles & Responsibilities *
            </label>
            <textarea
              id="requirements"
              value={formData.requirements || ''}
              onChange={(e) => setFormData({ ...formData, requirements: e.target.value })}
              rows={10}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium resize-none"
              placeholder="List the specific roles, responsibilities, and requirements for this position..."
              required
            />
            <p className="text-sm text-white/50 mt-3 font-medium">
              Be specific about what the AI agent needs to do and any required capabilities.
            </p>
          </div>

          <div className="flex justify-end gap-4">
            <button
              type="button"
              onClick={() => navigate(-1)}
              className="bg-dark-200/50 text-white/80 hover:text-white px-6 py-3 rounded-xl font-bold hover:bg-dark-200 border border-dark-300 transition-all duration-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-3 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
            >
              {isLoading ? (
                <span className="flex items-center gap-2">
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                  Creating...
                </span>
              ) : (
                'Post Position'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
