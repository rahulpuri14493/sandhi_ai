import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { agentsAPI } from '../lib/api'
import type { Agent } from '../lib/types'

export default function EditAgentPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [formData, setFormData] = useState<Partial<Agent>>({
    name: '',
    description: '',
    capabilities: [],
    pricing_model: 'pay_per_use',
    price_per_task: 0,
    price_per_communication: 0,
    monthly_price: undefined,
    quarterly_price: undefined,
    api_endpoint: '',
    api_key: '',
    llm_model: 'gpt-4o-mini',
    temperature: 0.7,
    a2a_enabled: false,
    status: 'pending',
  })
  const [newCapability, setNewCapability] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isLoadingAgent, setIsLoadingAgent] = useState(true)
  const [isTestingConnection, setIsTestingConnection] = useState(false)
  const [error, setError] = useState('')
  const [connectionTestResult, setConnectionTestResult] = useState<{
    success: boolean;
    message: string;
    warning?: string;
  } | null>(null)
  const [connectionValidated, setConnectionValidated] = useState(false)

  useEffect(() => {
    loadAgent()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const loadAgent = async () => {
    if (!id) return
    setIsLoadingAgent(true)
    try {
      const agent = await agentsAPI.get(parseInt(id))
      setFormData({
        name: agent.name || '',
        description: agent.description || '',
        capabilities: agent.capabilities || [],
        pricing_model: agent.pricing_model || 'pay_per_use',
        price_per_task: agent.price_per_task || 0,
        price_per_communication: agent.price_per_communication || 0,
        monthly_price: agent.monthly_price,
        quarterly_price: agent.quarterly_price,
        api_endpoint: agent.api_endpoint || '',
        api_key: agent.api_key || '', // This will be empty if not returned for security
        llm_model: agent.llm_model || 'gpt-4o-mini',
        temperature: agent.temperature ?? 0.7,
        a2a_enabled: agent.a2a_enabled ?? false,
        status: agent.status || 'pending',
      })
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load agent')
    } finally {
      setIsLoadingAgent(false)
    }
  }

  const handleTestConnection = async () => {
    if (!formData.api_endpoint) {
      setError('Please enter an API endpoint first')
      return
    }

    setIsTestingConnection(true)
    setError('')
    setConnectionTestResult(null)
    setConnectionValidated(false)

    try {
      const result = await agentsAPI.testConnection({
        api_endpoint: formData.api_endpoint!,
        api_key: formData.api_key || undefined,
        llm_model: formData.llm_model,
        temperature: formData.temperature,
        a2a_enabled: formData.a2a_enabled ?? false,
      })
      setConnectionTestResult(result)
      if (result.success) {
        setConnectionValidated(true)
      }
    } catch (err: any) {
      setConnectionTestResult({
        success: false,
        message: err.response?.data?.detail || 'Connection test failed'
      })
      setConnectionValidated(false)
    } finally {
      setIsTestingConnection(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!id) return
    
    // Warn if API endpoint is provided but not tested
    if (formData.api_endpoint && !connectionValidated) {
      const proceed = window.confirm(
        'You have an API endpoint but haven\'t tested the connection. Do you want to proceed anyway?'
      )
      if (!proceed) {
        return
      }
    }
    
    setIsLoading(true)
    setError('')
    try {
      // Only include api_key if it's been changed (not empty)
      const updateData = { ...formData }
      if (updateData.api_key === '') {
        // Don't send api_key if empty - backend will keep existing value
        delete updateData.api_key
      }
      await agentsAPI.update(parseInt(id), updateData)
      navigate('/dashboard')
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to update agent')
    } finally {
      setIsLoading(false)
    }
  }

  const addCapability = () => {
    if (newCapability.trim()) {
      setFormData({
        ...formData,
        capabilities: [...(formData.capabilities || []), newCapability.trim()],
      })
      setNewCapability('')
    }
  }

  const removeCapability = (index: number) => {
    setFormData({
      ...formData,
      capabilities: formData.capabilities?.filter((_, i) => i !== index),
    })
  }

  if (isLoadingAgent) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center justify-center min-h-[400px]">
            <div className="text-center">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
              <p className="text-white/60 text-lg font-semibold">Loading agent...</p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center gap-4 mb-10">
          <button
            onClick={() => navigate('/dashboard')}
            className="flex items-center text-white/70 hover:text-white transition-all duration-200 px-4 py-2.5 rounded-xl hover:bg-dark-200/50"
          >
            <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            <span className="font-semibold">Back</span>
          </button>
          <h1 className="text-6xl font-black text-white tracking-tight">Edit Agent</h1>
        </div>
        {error && (
          <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="name">
              Agent Name
            </label>
            <input
              id="name"
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              placeholder="Enter agent name..."
              required
            />
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="description">
              Description
            </label>
            <textarea
              id="description"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              rows={5}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium resize-none"
              placeholder="Describe what this agent does..."
            />
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg">
              Capabilities
            </label>
            <div className="flex gap-3 mb-3">
              <input
                type="text"
                value={newCapability}
                onChange={(e) => setNewCapability(e.target.value)}
                onKeyPress={(e) => e.key === 'Enter' && (e.preventDefault(), addCapability())}
                placeholder="Add capability..."
                className="flex-1 px-5 py-3 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 font-medium"
              />
              <button
                type="button"
                onClick={addCapability}
                className="bg-primary-500/20 text-primary-400 border border-primary-500/50 px-6 py-3 rounded-xl font-bold hover:bg-primary-500/30 transition-all duration-200"
              >
                Add
              </button>
            </div>
            <div className="flex flex-wrap gap-3">
              {formData.capabilities?.map((cap, idx) => (
                <span
                  key={idx}
                  className="px-4 py-2 bg-primary-500/20 text-primary-400 rounded-xl flex items-center gap-2 border border-primary-500/30 font-semibold"
                >
                  {cap}
                  <button
                    type="button"
                    onClick={() => removeCapability(idx)}
                    className="text-primary-300 hover:text-primary-200 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </span>
              ))}
            </div>
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="pricing_model">
              Pricing Model
            </label>
            <select
              id="pricing_model"
              value={formData.pricing_model || 'pay_per_use'}
              onChange={(e) =>
                setFormData({ ...formData, pricing_model: e.target.value as Agent['pricing_model'] })
              }
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              required
            >
              <option value="pay_per_use" className="bg-white text-gray-900">Pay Per Use (Task/Communication)</option>
              <option value="monthly" className="bg-white text-gray-900">Monthly Subscription</option>
              <option value="quarterly" className="bg-white text-gray-900">Quarterly Subscription</option>
            </select>
          </div>
          {formData.pricing_model === 'pay_per_use' && (
            <div className="grid md:grid-cols-2 gap-6 mb-8">
              <div>
                <label className="block text-white font-bold mb-3 text-lg" htmlFor="price_per_task">
                  Price per Task ($)
                </label>
                <input
                  id="price_per_task"
                  type="number"
                  step="0.01"
                  min="0"
                  value={formData.price_per_task}
                  onChange={(e) =>
                    setFormData({ ...formData, price_per_task: parseFloat(e.target.value) || 0 })
                  }
                  className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
                  required
                />
              </div>
              <div>
                <label
                  className="block text-white font-bold mb-3 text-lg"
                  htmlFor="price_per_communication"
                >
                  Price per Communication ($)
                </label>
                <input
                  id="price_per_communication"
                  type="number"
                  step="0.01"
                  min="0"
                  value={formData.price_per_communication}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      price_per_communication: parseFloat(e.target.value) || 0,
                    })
                  }
                  className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
                  required
                />
              </div>
            </div>
          )}
          {formData.pricing_model === 'monthly' && (
            <div className="mb-8">
              <label className="block text-white font-bold mb-3 text-lg" htmlFor="monthly_price">
                Monthly Price ($)
              </label>
              <input
                id="monthly_price"
                type="number"
                step="0.01"
                min="0"
                value={formData.monthly_price || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    monthly_price: e.target.value ? parseFloat(e.target.value) : undefined,
                  })
                }
                className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
                required
              />
            </div>
          )}
          {formData.pricing_model === 'quarterly' && (
            <div className="mb-8">
              <label className="block text-white font-bold mb-3 text-lg" htmlFor="quarterly_price">
                Quarterly Price ($)
              </label>
              <input
                id="quarterly_price"
                type="number"
                step="0.01"
                min="0"
                value={formData.quarterly_price || ''}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    quarterly_price: e.target.value ? parseFloat(e.target.value) : undefined,
                  })
                }
                className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
                required
              />
            </div>
          )}
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="llm_model">
              Model (optional)
            </label>
            <input
              id="llm_model"
              type="text"
              value={formData.llm_model || ''}
              onChange={(e) => setFormData({ ...formData, llm_model: e.target.value })}
              placeholder="e.g. gpt-4o-mini"
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium mb-6"
            />

            <label className="block text-white font-bold mb-3 text-lg" htmlFor="temperature">
              Temperature (optional)
            </label>
            <input
              id="temperature"
              type="number"
              min="0"
              max="2"
              step="0.1"
              value={formData.temperature ?? 0.7}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  temperature: e.target.value === '' ? undefined : parseFloat(e.target.value),
                })
              }
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
            />
            <p className="text-sm text-white/80 mt-3 font-medium">
              Used when calling OpenAI-compatible chat/completions endpoints. The platform calls your URL via its A2A adapter—no extra setup.
            </p>
          </div>

          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="api_endpoint">
              API Endpoint (optional)
            </label>
            <p className="text-sm text-white/60 mb-2 font-medium">
              Your endpoint URL. When used in jobs, the platform invokes it over A2A (directly if A2A-compliant, or via the platform’s adapter if OpenAI-compatible).
            </p>
            <div className="flex gap-3 mb-4">
              <input
                id="api_endpoint"
                type="url"
                value={formData.api_endpoint}
                onChange={(e) => {
                  setFormData({ ...formData, api_endpoint: e.target.value })
                  setConnectionValidated(false)
                  setConnectionTestResult(null)
                }}
                placeholder="https://api.openai.com/v1/chat/completions"
                className="flex-1 px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              />
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={isTestingConnection || !formData.api_endpoint}
                className="px-6 py-4 bg-gradient-to-r from-blue-500 to-blue-700 text-white rounded-xl font-bold hover:shadow-2xl hover:shadow-blue-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
                title={formData.a2a_enabled ? 'Test uses A2A SendMessage to your endpoint' : 'Test uses A2A via platform adapter (calls your OpenAI-compatible endpoint)'}
              >
                {isTestingConnection ? (
                  <span className="flex items-center gap-2">
                    <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                    Testing...
                  </span>
                ) : (
                  'Test Connection'
                )}
              </button>
            </div>
            {connectionTestResult && (
              <div className={`mb-4 p-4 rounded-xl border-2 ${
                connectionTestResult.success
                  ? 'bg-green-500/20 border-green-500/50 text-green-400'
                  : 'bg-red-500/20 border-red-500/50 text-red-400'
              }`}>
                <p className="font-bold">{connectionTestResult.message}</p>
                {connectionTestResult.warning && (
                  <p className="text-sm mt-2 text-yellow-400 font-medium">{connectionTestResult.warning}</p>
                )}
                {connectionValidated && (
                  <p className="text-sm mt-2 font-semibold">✓ Connection validated - you can proceed to save</p>
                )}
              </div>
            )}
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="api_key">
              API Key (optional)
            </label>
            <input
              id="api_key"
              type="password"
              value={formData.api_key}
              onChange={(e) => {
                setFormData({ ...formData, api_key: e.target.value })
                setConnectionValidated(false)
                setConnectionTestResult(null)
              }}
              placeholder="sk-... (leave empty to keep existing key)"
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
            />
            <p className="text-sm text-white/80 mt-3 font-medium">
              The API key will be sent in the Authorization header as: <code className="bg-dark-200/50 px-2 py-1 rounded text-primary-400 font-mono">Bearer YOUR_API_KEY</code>
              <br />
              <span className="text-white/80">Leave empty to keep the existing API key unchanged.</span>
            </p>
            <div className="mt-4 flex items-start gap-3">
              <input
                id="a2a_enabled"
                type="checkbox"
                checked={formData.a2a_enabled ?? false}
                onChange={(e) => setFormData({ ...formData, a2a_enabled: e.target.checked })}
                className="mt-1.5 h-5 w-5 rounded border-2 border-gray-300 text-primary-600 focus:ring-primary-500"
              />
              <div>
                <label htmlFor="a2a_enabled" className="text-white font-medium cursor-pointer block">
                  My endpoint is A2A protocol compliant (JSON-RPC 2.0)
                </label>
                <p className="text-sm text-white/60 mt-1 font-medium">
                  Check this only if your endpoint implements the A2A protocol (JSON-RPC SendMessage). If your endpoint is OpenAI-compatible (e.g. fine-tuned model), leave unchecked — the platform will call it via its internal A2A adapter so the architecture stays A2A. See docs/A2A_DEVELOPERS.md.
                </p>
              </div>
            </div>
            {formData.api_endpoint && !connectionValidated && (
              <p className="text-sm text-yellow-400 mt-3 font-semibold">
                ⚠️ Please test the connection before saving
              </p>
            )}
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="status">
              Status
            </label>
            <select
              id="status"
              value={formData.status}
              onChange={(e) => setFormData({ ...formData, status: e.target.value as any })}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
            >
              <option value="pending" className="bg-white text-gray-900">Pending</option>
              <option value="active" className="bg-white text-gray-900">Active</option>
              <option value="inactive" className="bg-white text-gray-900">Inactive</option>
            </select>
          </div>
          <div className="flex gap-4">
            <button
              type="submit"
              disabled={isLoading}
              className="flex-1 bg-gradient-to-r from-primary-500 to-primary-700 text-white py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                  Updating...
                </span>
              ) : (
                'Update Agent'
              )}
            </button>
            <button
              type="button"
              onClick={() => navigate('/dashboard')}
              className="px-6 py-4 bg-dark-200/50 text-white/80 hover:text-white border border-dark-300 rounded-xl font-bold hover:bg-dark-200 transition-all duration-200"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
