import { Routes, Route } from 'react-router-dom'
import { Navbar } from './components/Navbar'
import { ProtectedRoute } from './components/ProtectedRoute'
import Home from './pages/Home'
import Login from './pages/Login'
import Register from './pages/Register'
import Marketplace from './pages/Marketplace'
import AgentDetail from './pages/AgentDetail'
import NewJob from './pages/NewJob'
import JobDetail from './pages/JobDetail'
import Dashboard from './pages/Dashboard'
import NewAgent from './pages/NewAgent'
import EditAgent from './pages/EditAgent'
import EditJob from './pages/EditJob'
import NewHiringPosition from './pages/NewHiringPosition'
import MCP from './pages/MCP'

function App() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-dark-50 to-dark-100">
      <Navbar />
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/auth/login" element={<Login />} />
          <Route path="/auth/register" element={<Register />} />
          <Route path="/marketplace" element={<Marketplace />} />
          <Route path="/mcp" element={<MCP />} />
          <Route path="/marketplace/agent/:id" element={<AgentDetail />} />
          
          {/* Protected Routes */}
          <Route path="/jobs/new" element={
            <ProtectedRoute>
              <NewJob />
            </ProtectedRoute>
          } />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/jobs/edit/:id" element={
            <ProtectedRoute>
              <EditJob />
            </ProtectedRoute>
          } />
          <Route path="/dashboard" element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          } />
          <Route path="/agents/new" element={
            <ProtectedRoute>
              <NewAgent />
            </ProtectedRoute>
          } />
          <Route path="/agents/edit/:id" element={
            <ProtectedRoute>
              <EditAgent />
            </ProtectedRoute>
          } />
          <Route path="/hirings/new" element={
            <ProtectedRoute>
              <NewHiringPosition />
            </ProtectedRoute>
          } />
        </Routes>
      </main>
    </div>
  )
}

export default App
