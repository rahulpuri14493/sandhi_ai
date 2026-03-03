'use client'

import Link from 'next/link'
import { useAuthStore } from '@/lib/store'
import { useEffect } from 'react'

export function Navbar() {
  const { user, logout, loadUser } = useAuthStore()

  useEffect(() => {
    loadUser()
  }, [loadUser])

  return (
    <nav className="bg-white shadow-sm border-b">
      <div className="container mx-auto px-4">
        <div className="flex justify-between items-center h-16">
          <Link href="/" className="text-xl font-bold text-primary-600">
            Sandhi AI
          </Link>
          <div className="flex items-center gap-4">
            {user ? (
              <>
                <Link href="/marketplace" className="text-gray-700 hover:text-primary-600">
                  Marketplace
                </Link>
                {user.role === 'business' && (
                  <Link href="/jobs/new" className="text-gray-700 hover:text-primary-600">
                    New Job
                  </Link>
                )}
                <Link href="/dashboard" className="text-gray-700 hover:text-primary-600">
                  Dashboard
                </Link>
                <span className="text-gray-600">{user.email}</span>
                <button
                  onClick={logout}
                  className="text-gray-700 hover:text-primary-600"
                >
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link href="/auth/login" className="text-gray-700 hover:text-primary-600">
                  Login
                </Link>
                <Link
                  href="/auth/register"
                  className="bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700"
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
