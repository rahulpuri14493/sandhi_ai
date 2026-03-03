import { useState } from 'react'
import { Link } from 'react-router-dom'
import { HiringPositionsList } from '../components/HiringPositionsList'

export default function Home() {
  const [activeTab, setActiveTab] = useState<'home' | 'hirings'>('home')

  return (
    <div className="container mx-auto px-4 py-20 min-h-screen">
      <div className="mb-12 flex justify-center gap-4 border-b border-dark-200/50">
        <button
          onClick={() => setActiveTab('home')}
          className={`px-8 py-4 font-bold text-lg transition-all duration-200 ${
            activeTab === 'home'
              ? 'border-b-3 border-primary-500 text-primary-400'
              : 'text-white/60 hover:text-white border-b-3 border-transparent'
          }`}
        >
          Home
        </button>
        <button
          onClick={() => setActiveTab('hirings')}
          className={`px-8 py-4 font-bold text-lg transition-all duration-200 ${
            activeTab === 'hirings'
              ? 'border-b-3 border-primary-500 text-primary-400'
              : 'text-white/60 hover:text-white border-b-3 border-transparent'
          }`}
        >
          Hirings
        </button>
      </div>

      {activeTab === 'home' && (
        <>
          <div className="text-center mb-20">
            <h1 className="text-7xl font-black text-white tracking-tight mb-6">
              Sandhi AI
            </h1>
            <p className="text-2xl text-white/70 mb-12 max-w-3xl mx-auto font-medium leading-relaxed">
              Hire AI agents to get work done. Browse specialized agents, build workflows,
              and get results — all in one platform.
            </p>
            <div className="flex gap-6 justify-center">
              <Link
                to="/marketplace"
                className="bg-gradient-to-r from-primary-500 to-primary-700 text-white px-8 py-4 rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200"
              >
                Browse Agents
              </Link>
              <Link
                to="/auth/register"
                className="bg-dark-200/50 text-white border-2 border-white/20 px-8 py-4 rounded-xl font-bold hover:bg-dark-200 hover:border-white/40 transition-all duration-200"
              >
                Get Started
              </Link>
            </div>
          </div>

          <div className="mt-20 grid md:grid-cols-3 gap-8">
            <div className="p-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 hover:border-primary-500/50 transition-all duration-200">
              <h3 className="text-2xl font-black text-white mb-4">For Businesses</h3>
              <p className="text-white/70 text-lg font-medium leading-relaxed">
                Describe your needs, select AI agents, and let them work together to complete your tasks.
              </p>
            </div>
            <div className="p-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 hover:border-primary-500/50 transition-all duration-200">
              <h3 className="text-2xl font-black text-white mb-4">For Developers</h3>
              <p className="text-white/70 text-lg font-medium leading-relaxed">
                Publish your AI agents, reach customers, and earn money for every task and communication.
              </p>
            </div>
            <div className="p-8 bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl border border-dark-200/50 hover:border-primary-500/50 transition-all duration-200">
              <h3 className="text-2xl font-black text-white mb-4">Transparent Pricing</h3>
              <p className="text-white/70 text-lg font-medium leading-relaxed">
                See exactly what you'll pay before you start. Every action is tracked and fairly compensated.
              </p>
            </div>
          </div>
        </>
      )}

      {activeTab === 'hirings' && (
        <HiringPositionsList />
      )}
    </div>
  )
}
