import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../lib/store'
import { BusinessDashboard } from '../components/BusinessDashboard'
import { DeveloperDashboard } from '../components/DeveloperDashboard'

export default function DashboardPage() {
  const { user, loadUser } = useAuthStore()
  const navigate = useNavigate()
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (!user) {
      loadUser().then(() => {
        setIsLoading(false)
      })
    } else {
      setIsLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user])

  useEffect(() => {
    if (!isLoading && !user) {
      navigate('/auth/login')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, user])

  if (isLoading) {
    return (
      <div className="container mx-auto px-4 py-8 min-h-screen">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent mb-4"></div>
            <p className="text-white/60 text-lg font-semibold">Loading...</p>
          </div>
        </div>
      </div>
    )
  }

  if (!user) {
    return null
  }

  return (
    <div className="container mx-auto px-4 py-8 min-h-screen">
      {user.role === 'business' ? <BusinessDashboard /> : <DeveloperDashboard />}
    </div>
  )
}
