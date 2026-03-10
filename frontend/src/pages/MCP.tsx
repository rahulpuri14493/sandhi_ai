import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../lib/store'
import {
  mcpAPI,
  type MCPServerConnectionRes,
  type MCPToolConfigRes,
} from '../lib/api'

type View = 'choose' | 'connect' | 'connections' | 'configure' | 'tools'

export default function MCPPage() {
  const { user, loadUser } = useAuthStore()
  const navigate = useNavigate()
  const [view, setView] = useState<View>('choose')
  const [connections, setConnections] = useState<MCPServerConnectionRes[]>([])
  const [tools, setTools] = useState<MCPToolConfigRes[]>([])
  const [registryCount, setRegistryCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editTool, setEditTool] = useState<MCPToolConfigRes | null>(null)

  useEffect(() => {
    if (!user) {
      loadUser().then(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [user, loadUser])

  useEffect(() => {
    if (!loading && !user) {
      navigate('/auth/login')
    }
  }, [loading, user, navigate])

  useEffect(() => {
    if (!loading && user && user.role !== 'business') {
      navigate('/dashboard')
    }
  }, [loading, user, navigate])

  const loadConnections = () => {
    setError(null)
    mcpAPI.listConnections()
      .then((data) => { setConnections(data); setError(null); })
      .catch((err: { response?: { status?: number; data?: { detail?: string } } }) => {
        setConnections([])
        const msg = err.response?.status === 401 ? 'Session expired. Please log in again.' : (err.response?.data?.detail ?? 'Failed to load connections.')
        setError(msg)
      })
  }
  const loadTools = () => {
    setError(null)
    mcpAPI.listTools()
      .then((data) => { setTools(data); setError(null); })
      .catch((err: { response?: { status?: number; data?: { detail?: string } } }) => {
        setTools([])
        const msg = err.response?.status === 401 ? 'Session expired. Please log in again.' : (err.response?.data?.detail ?? 'Failed to load tools.')
        setError(msg)
      })
  }

  useEffect(() => {
    if (user && (view === 'connections' || view === 'connect' || view === 'choose')) loadConnections()
  }, [user, view])
  useEffect(() => {
    if (user && (view === 'tools' || view === 'configure' || view === 'choose')) loadTools()
  }, [user, view])

  useEffect(() => {
    if (user && view === 'choose') {
      mcpAPI.getRegistry().then((r) => setRegistryCount(r.tools?.length ?? 0)).catch(() => setRegistryCount(0))
    }
  }, [user, view])

  if (loading || !user || user.role !== 'business') {
    return (
      <div className="container mx-auto px-4 py-12 min-h-screen">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent" />
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto px-4 py-12 min-h-screen">
      <div className="mb-8">
        <Link
          to="/"
          className="text-white/70 hover:text-white font-medium mb-2 inline-block"
        >
          ← Back to Home
        </Link>
        <h1 className="text-4xl font-black text-white tracking-tight">
          MCP Server
        </h1>
        <p className="text-white/60 mt-1">
          View your configured tools and connections below, or connect an MCP server or add platform tools (Vector DB, PostgreSQL, File system, and more). Credentials are stored securely per account.
        </p>
        {registryCount !== null && registryCount > 0 && (
          <p className="text-primary-400/90 mt-2 text-sm font-medium">
            {registryCount} tool{registryCount !== 1 ? 's' : ''} available for agents in your jobs.
          </p>
        )}
      </div>

      {error && (
        <div className="mb-6 p-4 rounded-xl bg-red-500/20 border border-red-500/50 text-red-200 flex flex-wrap items-center justify-between gap-2">
          <span>{error}</span>
          {(error.includes('load') || error.includes('Failed to')) && (
            <div className="flex gap-2">
              {view === 'connections' && (
                <button type="button" onClick={loadConnections} className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-sm font-medium">
                  Retry connections
                </button>
              )}
              {view === 'tools' && (
                <button type="button" onClick={loadTools} className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-sm font-medium">
                  Retry tools
                </button>
              )}
              <button type="button" onClick={() => setError(null)} className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-sm font-medium">
                Dismiss
              </button>
            </div>
          )}
          {error.includes('Session expired') && (
            <Link to="/auth/login" className="px-3 py-1.5 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium">
              Log in again
            </Link>
          )}
          {!error.includes('load') && !error.includes('Failed to') && !error.includes('Session expired') && (
            <button type="button" onClick={() => setError(null)} className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-sm font-medium">
              Dismiss
            </button>
          )}
        </div>
      )}

      {view === 'choose' && (
        <div className="max-w-4xl space-y-8">
          {/* Primary: Add or manage — actions first */}
          <div>
            <p className="text-white/50 text-sm mb-4">Add or manage</p>
            <div className="grid md:grid-cols-2 gap-8">
              <button
                type="button"
                onClick={() => setView('connections')}
                className="text-left p-8 rounded-2xl bg-dark-100/80 border border-dark-200 hover:border-primary-500/50 hover:bg-dark-100 transition-all duration-200 group"
              >
            <div className="w-14 h-14 rounded-xl bg-primary-500/20 flex items-center justify-center mb-4 group-hover:bg-primary-500/30">
              <svg className="w-7 h-7 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
            </div>
            <h2 className="text-xl font-bold text-white mb-2">I have an MCP Server</h2>
            <p className="text-white/60">
              Connect from the platform using your existing MCP server URL and credentials. The platform will communicate with your server via API.
            </p>
          </button>
          <button
            type="button"
            onClick={() => setView('tools')}
            className="text-left p-8 rounded-2xl bg-dark-100/80 border border-dark-200 hover:border-primary-500/50 hover:bg-dark-100 transition-all duration-200 group"
          >
            <div className="w-14 h-14 rounded-xl bg-primary-500/20 flex items-center justify-center mb-4 group-hover:bg-primary-500/30">
              <svg className="w-7 h-7 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </div>
            <h2 className="text-xl font-bold text-white mb-2">Configure platform tools</h2>
            <p className="text-white/60">
              {tools.length > 0
                ? `${tools.length} tool${tools.length === 1 ? '' : 's'} configured. Manage or add more — they’re available to agents in job steps.`
                : 'Set up Vector Database, PostgreSQL, or File system tools in the platform MCP server. Credentials are stored encrypted per your account.'}
            </p>
          </button>
            </div>
          </div>

          {/* Secondary: Your configured items — summary below */}
          <div className="rounded-2xl border border-dark-200 overflow-hidden bg-dark-100/50">
            <div className="px-6 py-4 border-b border-dark-200 bg-dark-50/50">
              <h2 className="text-lg font-bold text-white">Your configured items</h2>
              <p className="text-sm text-white/60 mt-0.5">Platform tools and MCP connections available to your agents.</p>
            </div>
            <div className="grid md:grid-cols-2 gap-0 md:gap-6 md:divide-x md:divide-dark-200">
              <div className="p-6">
                <h3 className="text-sm font-semibold text-white/80 uppercase tracking-wider mb-3">Platform tools</h3>
                {tools.length === 0 ? (
                  <p className="text-white/50 text-sm mb-3">No tools configured yet.</p>
                ) : (
                  <ul className="space-y-2 mb-3">
                    {tools.slice(0, 5).map((t) => (
                      <li key={t.id} className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-white truncate">{t.name}</span>
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-primary-500/20 text-primary-300 border border-primary-500/30 shrink-0">
                          {TOOL_LABELS[t.tool_type] ?? t.tool_type}
                        </span>
                      </li>
                    ))}
                    {tools.length > 5 && (
                      <li className="text-white/50 text-sm">+{tools.length - 5} more</li>
                    )}
                  </ul>
                )}
                <button type="button" onClick={() => setView('tools')} className="text-sm font-medium text-primary-400 hover:text-primary-300">
                  {tools.length > 0 ? 'Manage tools →' : 'Configure platform tools →'}
                </button>
              </div>
              <div className="p-6">
                <h3 className="text-sm font-semibold text-white/80 uppercase tracking-wider mb-3">MCP connections</h3>
                {connections.length === 0 ? (
                  <p className="text-white/50 text-sm mb-3">No MCP servers connected yet.</p>
                ) : (
                  <ul className="space-y-2 mb-3">
                    {connections.slice(0, 5).map((c) => (
                      <li key={c.id} className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-white truncate">{c.name}</span>
                        <span className="text-white/50 text-sm truncate max-w-[180px]">{c.base_url}{c.endpoint_path && c.endpoint_path !== '/mcp' ? c.endpoint_path : ''}</span>
                      </li>
                    ))}
                    {connections.length > 5 && (
                      <li className="text-white/50 text-sm">+{connections.length - 5} more</li>
                    )}
                  </ul>
                )}
                <button type="button" onClick={() => setView('connections')} className="text-sm font-medium text-primary-400 hover:text-primary-300">
                  {connections.length > 0 ? 'Manage connections →' : 'Connect MCP server →'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {view === 'connect' && (
        <ConnectFlow
          onBack={() => setView('choose')}
          onSaved={() => { setView('connections'); loadConnections(); setError(null); }}
          onError={setError}
        />
      )}

      {view === 'connections' && (
        <ConnectionsList
          connections={connections}
          onBack={() => setView('choose')}
          onAdd={() => setView('connect')}
          onRefresh={loadConnections}
          onError={setError}
        />
      )}

      {view === 'configure' && (
        <ConfigureFlow
          editTool={editTool}
          onBack={() => { setView('tools'); setEditTool(null); }}
          onSaved={() => { setView('tools'); setEditTool(null); loadTools(); setError(null); }}
          onError={setError}
        />
      )}

      {view === 'tools' && (
        <ToolsList
          tools={tools}
          onBack={() => setView('choose')}
          onAdd={() => { setEditTool(null); setView('configure'); setError(null); }}
          onEdit={(tool) => { setEditTool(tool); setView('configure'); setError(null); }}
          onRefresh={loadTools}
          onError={setError}
        />
      )}
    </div>
  )
}

function ConnectFlow({
  onBack,
  onSaved,
  onError,
}: {
  onBack: () => void
  onSaved: () => void
  onError: (e: string | null) => void
}) {
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [endpointPath, setEndpointPath] = useState('/mcp')
  const [authType, setAuthType] = useState('none')
  const [token, setToken] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [validating, setValidating] = useState(false)
  const [validateMessage, setValidateMessage] = useState<{ success: boolean; text: string } | null>(null)

  const getCredentials = () =>
    authType === 'bearer' && token
      ? { token }
      : authType === 'api_key' && apiKey
        ? { api_key: apiKey }
        : authType === 'basic' && (username || password)
          ? { username, password }
          : undefined

  const handleValidate = async () => {
    onError(null)
    setValidateMessage(null)
    if (!name.trim() || !baseUrl.trim()) {
      setValidateMessage({ success: false, text: 'Name and Server URL are required to validate.' })
      return
    }
    setValidating(true)
    try {
      const res = await mcpAPI.validateConnection({
        name: name.trim(),
        base_url: baseUrl.trim(),
        endpoint_path: endpointPath || '/mcp',
        auth_type: authType,
        credentials: getCredentials(),
      })
      setValidateMessage({ success: res.valid, text: res.message })
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setValidateMessage({ success: false, text: detail ?? 'Validation request failed' })
    } finally {
      setValidating(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    onError(null)
    setValidateMessage(null)
    const credentials = getCredentials()
    setSubmitting(true)
    try {
      await mcpAPI.createConnection({
        name,
        base_url: baseUrl,
        endpoint_path: endpointPath || '/mcp',
        auth_type: authType,
        credentials,
      })
      onSaved()
    } catch (err: unknown) {
      onError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to create connection')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <button
        type="button"
        onClick={onBack}
        className="text-white/70 hover:text-white font-medium mb-6"
      >
        ← Back
      </button>
      <div className="p-6 rounded-2xl bg-dark-100/80 border border-dark-200">
        <h2 className="text-xl font-bold text-white mb-4">Connect your MCP Server</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-white/80 mb-1">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              placeholder="e.g. My MCP Server"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-white/80 mb-1">Server URL</label>
            <input
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              required
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              placeholder="https://mcp.example.com"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-white/80 mb-1">Endpoint path (optional)</label>
            <input
              type="text"
              value={endpointPath}
              onChange={(e) => setEndpointPath(e.target.value)}
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              placeholder="/mcp or /message or /"
            />
            <p className="text-white/50 text-xs mt-1">JSON-RPC endpoint path on the server (default /mcp)</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-white/80 mb-1">Authentication</label>
            <select
              value={authType}
              onChange={(e) => setAuthType(e.target.value)}
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white focus:ring-2 focus:ring-primary-500"
            >
              <option value="none">None</option>
              <option value="bearer">Bearer token</option>
              <option value="api_key">API key</option>
              <option value="basic">Basic (username/password)</option>
            </select>
          </div>
          {authType === 'bearer' && (
            <div>
              <label className="block text-sm font-medium text-white/80 mb-1">Token</label>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
                placeholder="Bearer token"
              />
            </div>
          )}
          {authType === 'api_key' && (
            <div>
              <label className="block text-sm font-medium text-white/80 mb-1">API key</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
                placeholder="API key"
              />
            </div>
          )}
          {authType === 'basic' && (
            <>
              <div>
                <label className="block text-sm font-medium text-white/80 mb-1">Username</label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-white/80 mb-1">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
                />
              </div>
            </>
          )}
          {validateMessage && (
            <div className={`p-3 rounded-xl text-sm ${validateMessage.success ? 'bg-green-500/20 border border-green-500/50 text-green-200' : 'bg-amber-500/20 border border-amber-500/50 text-amber-200'}`}>
              {validateMessage.text}
            </div>
          )}
          <div className="flex flex-wrap gap-3 pt-2">
            <button
              type="button"
              onClick={onBack}
              className="px-5 py-2.5 rounded-xl border border-dark-200 text-white/90 hover:bg-dark-200/50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleValidate}
              disabled={validating}
              className="px-5 py-2.5 rounded-xl border border-primary-500/70 text-primary-400 hover:bg-primary-500/20 disabled:opacity-50"
            >
              {validating ? 'Checking...' : 'Validate connection'}
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-primary-500 to-primary-700 text-white font-semibold hover:shadow-lg hover:shadow-primary-500/30 disabled:opacity-50"
            >
              {submitting ? 'Connecting...' : 'Connect'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ConnectionsList({
  connections,
  onBack,
  onAdd,
  onRefresh,
  onError,
}: {
  connections: MCPServerConnectionRes[]
  onBack: () => void
  onAdd: () => void
  onRefresh: () => void
  onError: (e: string | null) => void
}) {
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const handleDeleteClick = (id: number) => {
    setConfirmDeleteId(id)
    onError(null)
  }

  const handleDeleteConfirm = async () => {
    if (confirmDeleteId == null) return
    setDeletingId(confirmDeleteId)
    setConfirmDeleteId(null)
    onError(null)
    try {
      await mcpAPI.deleteConnection(confirmDeleteId)
      onRefresh()
    } catch {
      onError('Failed to delete connection')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <button type="button" onClick={onBack} className="text-white/70 hover:text-white font-medium">
          ← Back
        </button>
        <button
          type="button"
          onClick={onAdd}
          className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-primary-500 to-primary-700 text-white font-semibold hover:shadow-lg hover:shadow-primary-500/30"
        >
          + New connection
        </button>
      </div>
      <div className="rounded-2xl border border-dark-200 overflow-hidden">
        <div className="bg-primary-500/20 px-6 py-3 border-b border-dark-200">
          <h2 className="text-lg font-bold text-white">Your MCP connections</h2>
        </div>
        {connections.length === 0 ? (
          <div className="p-8 text-center text-white/60">
            No connections yet. Add one to connect your MCP server from the platform.
          </div>
        ) : (
          <ul className="divide-y divide-dark-200">
            {connections.map((c) => (
              <li key={c.id} className="px-6 py-4 flex items-center justify-between bg-dark-50/50">
                <div>
                  <span className="font-medium text-white">{c.name}</span>
                  <span className="text-white/50 mx-2">·</span>
                  <span className="text-white/60 text-sm">{c.base_url}{c.endpoint_path && c.endpoint_path !== '/mcp' ? c.endpoint_path : ''}</span>
                  {!c.is_active && (
                    <span className="ml-2 text-xs px-2 py-0.5 rounded bg-dark-200 text-white/60">Inactive</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {confirmDeleteId === c.id ? (
                    <>
                      <span className="text-white/60 text-sm">Delete this connection?</span>
                      <button type="button" onClick={handleDeleteConfirm} disabled={deletingId === c.id} className="px-3 py-1.5 rounded-lg bg-red-500/80 hover:bg-red-500 text-white text-sm font-medium disabled:opacity-50">
                        {deletingId === c.id ? 'Deleting...' : 'Yes, delete'}
                      </button>
                      <button type="button" onClick={() => setConfirmDeleteId(null)} className="px-3 py-1.5 rounded-lg border border-dark-200 text-white/80 hover:bg-dark-200/50 text-sm font-medium">
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button type="button" onClick={() => handleDeleteClick(c.id)} disabled={deletingId !== null} className="px-3 py-1.5 rounded-lg text-red-400 hover:bg-red-500/20 text-sm font-medium disabled:opacity-50">
                      Delete
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

const TOOL_LABELS: Record<string, string> = {
  vector_db: 'Vector Database (generic)',
  pinecone: 'Pinecone',
  weaviate: 'Weaviate',
  qdrant: 'Qdrant',
  chroma: 'Chroma',
  postgres: 'PostgreSQL',
  mysql: 'MySQL',
  elasticsearch: 'Elasticsearch',
  filesystem: 'File system',
  s3: 'AWS S3',
  slack: 'Slack',
  github: 'GitHub',
  notion: 'Notion',
  rest_api: 'REST API',
}

const TOOL_OPTIONS_GROUPS: { label: string; options: { value: string; label: string }[] }[] = [
  { label: 'Vector stores', options: [
    { value: 'pinecone', label: 'Pinecone' },
    { value: 'weaviate', label: 'Weaviate' },
    { value: 'qdrant', label: 'Qdrant' },
    { value: 'chroma', label: 'Chroma' },
    { value: 'vector_db', label: 'Vector Database (generic)' },
  ]},
  { label: 'Databases', options: [
    { value: 'postgres', label: 'PostgreSQL' },
    { value: 'mysql', label: 'MySQL' },
  ]},
  { label: 'Search', options: [
    { value: 'elasticsearch', label: 'Elasticsearch' },
  ]},
  { label: 'Storage', options: [
    { value: 'filesystem', label: 'File system' },
    { value: 's3', label: 'AWS S3' },
  ]},
  { label: 'Integrations', options: [
    { value: 'slack', label: 'Slack' },
    { value: 'github', label: 'GitHub' },
    { value: 'notion', label: 'Notion' },
    { value: 'rest_api', label: 'REST API' },
  ]},
]

function ConfigureFlow({
  editTool,
  onBack,
  onSaved,
  onError,
}: {
  editTool: MCPToolConfigRes | null
  onBack: () => void
  onSaved: () => void
  onError: (e: string | null) => void
}) {
  const [toolType, setToolType] = useState(editTool?.tool_type ?? 'vector_db')
  const [name, setName] = useState(editTool?.name ?? '')
  const [config, setConfig] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [validating, setValidating] = useState(false)
  const [validateMessage, setValidateMessage] = useState<{ success: boolean; text: string } | null>(null)
  const [toolTypeOpen, setToolTypeOpen] = useState(false)
  const [toolTypeSearch, setToolTypeSearch] = useState('')
  const toolTypeRef = useRef<HTMLDivElement>(null)
  const toolTypeSearchInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!toolTypeOpen) return
    setToolTypeSearch('')
    toolTypeSearchInputRef.current?.focus()
    const onDocClick = (e: MouseEvent) => {
      if (toolTypeRef.current && !toolTypeRef.current.contains(e.target as Node)) setToolTypeOpen(false)
    }
    document.addEventListener('click', onDocClick)
    return () => document.removeEventListener('click', onDocClick)
  }, [toolTypeOpen])

  useEffect(() => {
    if (editTool) {
      setName(editTool.name)
      setToolType(editTool.tool_type)
      setConfig({})
    } else {
      setName('')
      setToolType('vector_db')
      setConfig({})
    }
    setValidateMessage(null)
  }, [editTool])

  const configFields: Record<string, { key: string; label: string; placeholder: string; secret?: boolean }[]> = {
    vector_db: [
      { key: 'api_key', label: 'API key', placeholder: 'Your API key', secret: true },
      { key: 'url', label: 'URL / endpoint', placeholder: 'https://...' },
      { key: 'index_name', label: 'Index name (optional)', placeholder: 'my-index' },
    ],
    pinecone: [
      { key: 'api_key', label: 'Pinecone API key', placeholder: 'From Pinecone console', secret: true },
      { key: 'url', label: 'Pinecone host', placeholder: 'https://xxx.pinecone.io' },
      { key: 'index_name', label: 'Index name', placeholder: 'default' },
    ],
    weaviate: [
      { key: 'api_key', label: 'Weaviate API key (optional)', placeholder: '', secret: true },
      { key: 'url', label: 'Weaviate URL', placeholder: 'http://localhost:8080' },
      { key: 'index_name', label: 'Class name (optional)', placeholder: 'Document' },
    ],
    qdrant: [
      { key: 'api_key', label: 'API key (optional)', placeholder: '', secret: true },
      { key: 'url', label: 'Qdrant URL', placeholder: 'http://localhost:6333' },
      { key: 'index_name', label: 'Collection name', placeholder: 'my_collection' },
    ],
    chroma: [
      { key: 'url', label: 'Chroma URL', placeholder: 'http://localhost:8000' },
      { key: 'index_name', label: 'Collection name (optional)', placeholder: 'default' },
    ],
    postgres: [
      { key: 'connection_string', label: 'Connection string', placeholder: 'postgresql://user:pass@host:5432/db', secret: true },
      { key: 'schema', label: 'Schema (optional)', placeholder: 'public' },
    ],
    mysql: [
      { key: 'host', label: 'Host', placeholder: 'localhost' },
      { key: 'port', label: 'Port', placeholder: '3306' },
      { key: 'user', label: 'User', placeholder: 'root', secret: false },
      { key: 'password', label: 'Password', placeholder: '', secret: true },
      { key: 'database', label: 'Database', placeholder: 'mydb' },
    ],
    elasticsearch: [
      { key: 'url', label: 'Elasticsearch URL', placeholder: 'http://localhost:9200' },
      { key: 'api_key', label: 'API key (optional)', placeholder: '', secret: true },
    ],
    filesystem: [
      { key: 'base_path', label: 'Base path', placeholder: '/data or C:\\data' },
      { key: 'allowed_extensions', label: 'Allowed extensions (optional)', placeholder: '.txt,.md,.json' },
    ],
    s3: [
      { key: 'bucket', label: 'Bucket name', placeholder: 'my-bucket' },
      { key: 'region', label: 'Region', placeholder: 'us-east-1' },
      { key: 'access_key_id', label: 'Access key ID', placeholder: '', secret: true },
      { key: 'secret_access_key', label: 'Secret access key', placeholder: '', secret: true },
    ],
    slack: [
      { key: 'bot_token', label: 'Bot token (xoxb-...)', placeholder: '', secret: true },
      { key: 'default_channel', label: 'Default channel ID (optional)', placeholder: 'C01234...' },
    ],
    github: [
      { key: 'api_key', label: 'Personal access token', placeholder: 'ghp_...', secret: true },
      { key: 'base_url', label: 'GitHub API URL (optional)', placeholder: 'https://api.github.com' },
    ],
    notion: [
      { key: 'api_key', label: 'Notion integration token', placeholder: 'secret_...', secret: true },
      { key: 'base_url', label: 'Notion API URL (optional)', placeholder: 'https://api.notion.com' },
    ],
    rest_api: [
      { key: 'base_url', label: 'Base URL', placeholder: 'https://api.example.com' },
      { key: 'api_key', label: 'API key / Bearer token (optional)', placeholder: '', secret: true },
    ],
  }

  const fields = configFields[toolType] ?? configFields.vector_db

  const buildConfigPayload = (): Record<string, unknown> => {
    const payload: Record<string, unknown> = {}
    fields.forEach((f) => {
      const v = config[f.key]?.trim()
      if (v !== undefined && v !== '') payload[f.key] = v
    })
    return payload
  }

  const handleValidate = async () => {
    onError(null)
    setValidateMessage(null)
    const configPayload = buildConfigPayload()
    if (editTool && Object.keys(configPayload).length === 0) {
      setValidateMessage({ success: false, text: 'Enter connection details to validate, or leave blank when editing to keep existing.' })
      return
    }
    if (!editTool && Object.keys(configPayload).length === 0) {
      setValidateMessage({ success: false, text: 'Enter connection details to validate.' })
      return
    }
    setValidating(true)
    try {
      const res = await mcpAPI.validateToolConfig(toolType, configPayload)
      setValidateMessage({ success: res.valid, text: res.message })
    } catch (err: unknown) {
      setValidateMessage({ success: false, text: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Validation request failed' })
    } finally {
      setValidating(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    onError(null)
    setValidateMessage(null)
    if (!editTool) {
      if (toolType === 'postgres' && !config.connection_string?.trim()) {
        setValidateMessage({ success: false, text: 'PostgreSQL requires a connection string.' })
        return
      }
      if (toolType === 'mysql' && (!config.host?.trim() || !config.database?.trim())) {
        setValidateMessage({ success: false, text: 'MySQL requires host and database.' })
        return
      }
    }
    const configPayload = buildConfigPayload()
    setSubmitting(true)
    try {
      if (editTool) {
        await mcpAPI.updateTool(editTool.id, { name, ...(Object.keys(configPayload).length > 0 ? { config: configPayload } : {}) })
      } else {
        await mcpAPI.createTool({ tool_type: toolType, name, config: configPayload })
      }
      onSaved()
    } catch (err: unknown) {
      const res = err as { response?: { status?: number; data?: { detail?: string } } }
      if (res.response?.status === 404) {
        onError('This tool was deleted.')
        onSaved()
        return
      }
      onError(res.response?.data?.detail ?? (editTool ? 'Failed to update tool config' : 'Failed to create tool config'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <button type="button" onClick={onBack} className="text-white/70 hover:text-white font-medium mb-6">
        ← Back
      </button>
      <div className="p-6 rounded-2xl bg-dark-100/80 border border-dark-200">
        <h2 className="text-xl font-bold text-white mb-4">{editTool ? 'Edit platform MCP tool' : 'Configure platform MCP tool'}</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div ref={toolTypeRef} className="relative">
            <label className="block text-sm font-medium text-white/80 mb-1">Tool type</label>
            <button
              type="button"
              onClick={() => !editTool && setToolTypeOpen((o) => !o)}
              disabled={!!editTool}
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500 disabled:opacity-70 disabled:cursor-not-allowed text-left flex items-center justify-between"
            >
              <span>{TOOL_LABELS[toolType] ?? toolType}</span>
              {!editTool && (
                <svg className={`w-4 h-4 text-white/60 transition-transform ${toolTypeOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              )}
            </button>
            {!editTool && toolTypeOpen && (
              <div className="absolute top-full left-0 right-0 mt-1 z-50 rounded-xl border border-dark-200 bg-dark-100 shadow-xl max-h-80 overflow-hidden flex flex-col">
                <div className="p-2 border-b border-dark-200 bg-dark-50/80 shrink-0">
                  <input
                    ref={toolTypeSearchInputRef}
                    type="text"
                    value={toolTypeSearch}
                    onChange={(e) => setToolTypeSearch(e.target.value)}
                    onKeyDown={(e) => e.stopPropagation()}
                    placeholder="Search tool type..."
                    className="w-full px-3 py-2 rounded-lg bg-dark-100 border border-dark-200 text-white placeholder-white/40 text-sm focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>
                <div className="overflow-y-auto max-h-64">
                  {(() => {
                    const q = toolTypeSearch.trim().toLowerCase()
                    const filtered = TOOL_OPTIONS_GROUPS.map((grp) => ({
                      ...grp,
                      options: q ? grp.options.filter((o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)) : grp.options,
                    })).filter((grp) => grp.options.length > 0)
                    if (filtered.length === 0) {
                      return (
                        <div className="px-4 py-6 text-center text-white/50 text-sm">No matching tool types. Try a different search.</div>
                      )
                    }
                    return filtered.map((grp) => (
                      <div key={grp.label} className="border-b border-dark-200 last:border-b-0">
                        <div className="px-4 py-1.5 text-xs font-semibold text-white/50 uppercase tracking-wider bg-dark-50/80 sticky top-0">{grp.label}</div>
                        {grp.options.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => { setToolType(opt.value); setConfig({}); setToolTypeOpen(false); }}
                            className={`block w-full px-4 py-2.5 text-left text-sm ${toolType === opt.value ? 'bg-primary-500/30 text-primary-200' : 'text-white/90 hover:bg-dark-200/50 hover:text-white'}`}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                    ))
                  })()}
                </div>
              </div>
            )}
          </div>
          <div>
            <label className="block text-sm font-medium text-white/80 mb-1">Name (max 255 characters)</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={255}
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
              placeholder="e.g. My Pinecone index"
            />
          </div>
          {fields.map((f) => (
            <div key={f.key}>
              <label className="block text-sm font-medium text-white/80 mb-1">{f.label}</label>
              <input
                type={f.secret ? 'password' : 'text'}
                value={config[f.key] ?? ''}
                onChange={(e) => setConfig((prev) => ({ ...prev, [f.key]: e.target.value }))}
                className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
                placeholder={editTool && f.secret ? 'Leave blank to keep existing' : f.placeholder}
              />
              {toolType === 'postgres' && f.key === 'connection_string' && (
                <p className="mt-1 text-xs text-white/50">If the app runs in Docker, use <code className="bg-dark-200/50 px-1 rounded">host.docker.internal</code> instead of localhost to reach PostgreSQL on your host.</p>
              )}
            </div>
          ))}
          {validateMessage && (
            <div className={`p-3 rounded-xl text-sm ${validateMessage.success ? 'bg-green-500/20 border border-green-500/50 text-green-200' : 'bg-amber-500/20 border border-amber-500/50 text-amber-200'}`}>
              {validateMessage.text}
            </div>
          )}
          <div className="flex flex-wrap gap-3 pt-2">
            <button type="button" onClick={onBack} className="px-5 py-2.5 rounded-xl border border-dark-200 text-white/90 hover:bg-dark-200/50">
              Cancel
            </button>
            <button
              type="button"
              onClick={handleValidate}
              disabled={validating}
              className="px-5 py-2.5 rounded-xl border border-primary-500/70 text-primary-400 hover:bg-primary-500/20 disabled:opacity-50"
            >
              {validating ? 'Validating...' : 'Validate connection'}
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-primary-500 to-primary-700 text-white font-semibold hover:shadow-lg hover:shadow-primary-500/30 disabled:opacity-50"
            >
              {submitting ? (editTool ? 'Updating...' : 'Saving...') : editTool ? 'Update' : 'Save (credentials stored encrypted)'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ToolsList({
  tools,
  onBack,
  onAdd,
  onEdit,
  onRefresh,
  onError,
}: {
  tools: MCPToolConfigRes[]
  onBack: () => void
  onAdd: () => void
  onEdit: (tool: MCPToolConfigRes) => void
  onRefresh: () => void
  onError: (e: string | null) => void
}) {
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const handleDeleteClick = (id: number) => {
    setConfirmDeleteId(id)
    onError(null)
  }

  const handleDeleteConfirm = async () => {
    if (confirmDeleteId == null) return
    setDeletingId(confirmDeleteId)
    setConfirmDeleteId(null)
    onError(null)
    try {
      await mcpAPI.deleteTool(confirmDeleteId)
      onRefresh()
    } catch {
      onError('Failed to delete tool config')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <button type="button" onClick={onBack} className="text-white/70 hover:text-white font-medium">
          ← Back
        </button>
        <button
          type="button"
          onClick={onAdd}
          className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-primary-500 to-primary-700 text-white font-semibold hover:shadow-lg hover:shadow-primary-500/30"
        >
          + New tool
        </button>
      </div>

      <div className="rounded-2xl border border-dark-200 overflow-hidden">
        <div className="bg-primary-500/20 px-6 py-4 border-b border-dark-200">
          <h2 className="text-lg font-bold text-white">Platform MCP tools</h2>
          <p className="text-sm text-white/70 mt-1">
            {tools.length === 0
              ? 'Add tools to make them available to agents in your job steps.'
              : `${tools.length} tool${tools.length === 1 ? '' : 's'} configured — available to your agents in job steps.`}
          </p>
        </div>

        {tools.length === 0 ? (
          <div className="p-12 text-center">
            <div className="w-16 h-16 rounded-2xl bg-dark-200/50 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-white/40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </div>
            <p className="text-white/70 font-medium mb-1">No tools configured yet</p>
            <p className="text-white/50 text-sm max-w-sm mx-auto mb-6">
              Add Vector DB, PostgreSQL, File system, or other integrations. They will appear here and be available to agents when you run jobs.
            </p>
            <button
              type="button"
              onClick={onAdd}
              className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-primary-500 to-primary-700 text-white font-semibold hover:shadow-lg hover:shadow-primary-500/30"
            >
              + Add your first tool
            </button>
          </div>
        ) : (
          <div className="p-4 sm:p-6 grid gap-4">
            {tools.map((t) => (
              <div
                key={t.id}
                className="rounded-xl border border-dark-200 bg-dark-50/50 hover:border-dark-200/80 transition-colors p-4 sm:p-5 flex flex-wrap items-center justify-between gap-4"
              >
                <div className="flex flex-wrap items-center gap-3 min-w-0">
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium bg-primary-500/20 text-primary-300 border border-primary-500/30">
                      {TOOL_LABELS[t.tool_type] ?? t.tool_type}
                    </span>
                    <span className="font-semibold text-white truncate">{t.name}</span>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    {t.is_active ? (
                      <span className="inline-flex items-center gap-1.5 text-green-400/90">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-400" aria-hidden />
                        Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-white/50">
                        <span className="w-1.5 h-1.5 rounded-full bg-white/40" aria-hidden />
                        Inactive
                      </span>
                    )}
                    <span className="text-white/40">·</span>
                    <span className="text-white/50">Available in job steps</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => onEdit(t)}
                    className="px-4 py-2 rounded-lg border border-dark-200 text-white/90 hover:bg-dark-200/50 hover:text-white text-sm font-medium transition-colors"
                  >
                    Edit
                  </button>
                  {confirmDeleteId === t.id ? (
                    <>
                      <span className="text-white/50 text-sm hidden sm:inline">Delete?</span>
                      <button type="button" onClick={handleDeleteConfirm} disabled={deletingId === t.id} className="px-4 py-2 rounded-lg bg-red-500/80 hover:bg-red-500 text-white text-sm font-medium disabled:opacity-50">
                        {deletingId === t.id ? 'Deleting...' : 'Yes, delete'}
                      </button>
                      <button type="button" onClick={() => setConfirmDeleteId(null)} className="px-4 py-2 rounded-lg border border-dark-200 text-white/80 hover:bg-dark-200/50 text-sm font-medium">
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => handleDeleteClick(t.id)}
                      disabled={deletingId !== null}
                      className="px-4 py-2 rounded-lg text-red-400 hover:bg-red-500/20 hover:text-red-300 text-sm font-medium disabled:opacity-50 transition-colors"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
