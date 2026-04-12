import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuthStore } from '../lib/store'
import { isAxiosError } from 'axios'

function formatLoginError(err: unknown): string {
  if (isAxiosError(err)) {
    const d = err.response?.data?.detail
    if (typeof d === 'string') return d
    if (Array.isArray(d)) {
      return d.map((e) => (typeof e === 'object' && e && 'msg' in e ? String((e as { msg: string }).msg) : JSON.stringify(e))).join(' ')
    }
    if (err.code === 'ERR_NETWORK' || err.message === 'Network Error') {
      return 'Cannot reach the API. Is the backend running on port 8000 and Vite proxy pointing to it?'
    }
  }
  if (err instanceof Error && err.message) return err.message
  return 'Login failed'
}

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const { login, isLoading } = useAuthStore()
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await login(email, password)
      navigate('/dashboard')
    } catch (err: unknown) {
      setError(formatLoginError(err))
    }
  }

  return (
    <div className="container mx-auto px-4 py-16 min-h-screen flex items-center justify-center">
      <div className="max-w-md w-full bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-10 border border-dark-200/50">
        <h1 className="text-5xl font-black text-white tracking-tight mb-8">Login</h1>
        {error && (
          <div className="bg-red-500/20 border-2 border-red-500/50 text-red-400 px-6 py-4 rounded-2xl mb-6 font-semibold">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit}>
          <div className="mb-6">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              placeholder="Enter your email"
              required
            />
          </div>
          <div className="mb-8">
            <label className="block text-white font-bold mb-3 text-lg" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium"
              placeholder="Enter your password"
              required
            />
          </div>
          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-gradient-to-r from-primary-500 to-primary-700 text-white py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
          >
            {isLoading ? (
              <span className="flex items-center justify-center gap-2">
                <div className="animate-spin rounded-full h-5 w-5 border-2 border-white border-t-transparent"></div>
                Logging in...
              </span>
            ) : (
              'Login'
            )}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-white/60 font-medium">
          Don't have an account?{' '}
          <Link to="/auth/register" className="text-primary-400 hover:text-primary-300 font-bold">
            Sign up
          </Link>
        </p>
      </div>
    </div>
  )
}
