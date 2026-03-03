import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../lib/store'
import { useEffect } from 'react'
import logoSrc from '../assets/sandhi-ai-logo.svg'

export function Navbar() {
  const { user, logout, loadUser } = useAuthStore()
  const navigate = useNavigate()

  useEffect(() => {
    loadUser()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleLogout = () => {
    logout()
    navigate('/')
  }

  return (
    <nav className="bg-dark-50/80 backdrop-blur-xl border-b border-dark-200/50 sticky top-0 z-50 shadow-lg">
      <div className="container mx-auto px-4">
        <div className="flex justify-between items-center min-h-[5rem] py-2">
          <Link to="/" className="flex items-center gap-0 group no-underline">
            <img
              src={logoSrc}
              alt="Sandhi AI"
              className="h-16 w-auto object-contain object-center block"
            />
          </Link>
          <div className="flex items-center gap-1">
            {user ? (
              <>
                <Link 
                  to="/marketplace" 
                  className="px-5 py-2.5 text-white/90 hover:text-white font-semibold rounded-xl hover:bg-dark-100/50 transition-all duration-200"
                >
                  Marketplace
                </Link>
                {user.role === 'business' && (
                  <Link 
                    to="/jobs/new" 
                    className="px-5 py-2.5 text-white/90 hover:text-white font-semibold rounded-xl hover:bg-dark-100/50 transition-all duration-200"
                  >
                    New Job
                  </Link>
                )}
                <Link 
                  to="/dashboard" 
                  className="px-5 py-2.5 text-white/90 hover:text-white font-semibold rounded-xl hover:bg-dark-100/50 transition-all duration-200"
                >
                  Dashboard
                </Link>
                <div className="h-10 w-px bg-dark-200 mx-3"></div>
                <div className="flex items-center gap-3">
                  <div className="px-4 py-2 bg-dark-100 rounded-xl border border-dark-200">
                    <span className="text-sm text-white/80 font-medium">{user.email}</span>
                  </div>
                  <button
                    onClick={handleLogout}
                    className="px-5 py-2.5 text-white/80 hover:text-white font-semibold rounded-xl hover:bg-red-500/20 transition-all duration-200"
                  >
                    Logout
                  </button>
                </div>
              </>
            ) : (
              <>
                <Link 
                  to="/auth/login" 
                  className="px-5 py-2.5 text-white/90 hover:text-white font-semibold rounded-xl hover:bg-dark-100/50 transition-all duration-200"
                >
                  Login
                </Link>
                <Link
                  to="/auth/register"
                  className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-6 py-2.5 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
                >
                  Sign Up
                </Link>
              </>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}
