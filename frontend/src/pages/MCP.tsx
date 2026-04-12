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
  const [platformRegistryTools, setPlatformRegistryTools] = useState<Array<{ source: string; id?: number; name: string; tool_type?: string; description?: string; access_mode?: 'read_only' | 'read_write' }>>([])
  const [connectionRegistryTools, setConnectionRegistryTools] = useState<Array<{ connection_id: number; name: string; base_url: string; tools: Array<{ name: string; description?: string }>; error?: string }>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editTool, setEditTool] = useState<MCPToolConfigRes | null>(null)
  const [editConnection, setEditConnection] = useState<MCPServerConnectionRes | null>(null)

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
      mcpAPI.getRegistry()
        .then((r) => {
          setPlatformRegistryTools(r.platform_tools ?? [])
          setConnectionRegistryTools(r.connection_tools ?? [])
          const connectionToolsCount = (r.connection_tools ?? []).reduce((sum, conn) => sum + (conn.tools?.length ?? 0), 0)
          const total = (r.platform_tools?.length ?? 0) + connectionToolsCount
          setRegistryCount(total)
        })
        .catch(() => {
          setPlatformRegistryTools([])
          setConnectionRegistryTools([])
          setRegistryCount(0)
        })
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
          <>
            <p className="text-primary-400/90 mt-2 text-sm font-medium">
              {registryCount} tool{registryCount !== 1 ? 's' : ''} available for agents in your jobs.
            </p>
            {connectionRegistryTools.length > 0 && (
              <div className="mt-3 p-4 rounded-xl bg-dark-100/80 border border-dark-200">
                <h3 className="text-sm font-semibold text-white/90 mb-2">Tools from your MCP connections</h3>
                <p className="text-white/50 text-xs mb-3">Tools exposed by each external MCP server you connected.</p>
                <div className="space-y-4">
                  {connectionRegistryTools.map((conn) => (
                    <div key={conn.connection_id} className="rounded-lg bg-dark-200/60 border border-dark-300 p-3">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="font-medium text-white">{conn.name}</span>
                        <span className="text-white/50 text-xs truncate" title={conn.base_url}>{conn.base_url}</span>
                      </div>
                      {conn.error && (
                        <p className="text-amber-400/90 text-xs mb-2">Could not load tools: {conn.error}</p>
                      )}
                      {conn.tools && conn.tools.length > 0 ? (
                        <ul className="flex flex-wrap gap-2">
                          {conn.tools.map((tool, idx) => (
                            <li
                              key={`${conn.connection_id}-${tool.name}-${idx}`}
                              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-dark-300/60 border border-dark-400 text-sm"
                            >
                              <span className="font-medium text-white truncate max-w-[200px]" title={tool.description ?? tool.name}>{tool.name}</span>
                              {tool.description && (
                                <span className="text-white/50 text-xs max-w-[180px] truncate" title={tool.description}>{tool.description}</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      ) : !conn.error && (
                        <p className="text-white/50 text-xs">No tools returned from this server.</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {platformRegistryTools.length > 0 && (
              <div className="mt-3 p-4 rounded-xl bg-dark-100/80 border border-dark-200">
                <h3 className="text-sm font-semibold text-white/90 mb-2">Platform tools (internal MCP server)</h3>
                <p className="text-white/50 text-xs mb-3">
                  Vector DB, PostgreSQL, file storage, and other tools run by the platform MCP server for your account.
                  {' '}
                  <span className="text-white/40">Badges reflect each tool’s interactive capabilities (read-only vs read & write).</span>
                </p>
                <div className="space-y-4">
                  <div className="rounded-lg bg-dark-200/60 border border-dark-300 p-3">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <span className="font-medium text-white">Platform MCP</span>
                      <span className="text-white/50 text-xs">Internal · one server, your configured tools</span>
                    </div>
                    <ul className="flex flex-wrap gap-2">
                      {platformRegistryTools.map((t, idx) => {
                        const secondary =
                          (t.description && t.description.trim()) ||
                          (t.tool_type ? (TOOL_LABELS[t.tool_type] ?? t.tool_type) : '')
                        const mode = resolvePlatformRegistryAccessMode(t)
                        const isReadWrite = mode === 'read_write'
                        const tt = (t.tool_type || '').toLowerCase()
                        const readWriteHint =
                          tt === 'postgres' || tt === 'mysql'
                            ? 'SELECT/WITH use a read-only session; other SQL (DML/DDL) is committed — you can write to Postgres through this tool.'
                            : 'This tool supports both read and write operations in the platform MCP server.'
                        return (
                          <li
                            key={`platform-${t.id ?? idx}`}
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-dark-300/60 border border-dark-400 text-sm flex-wrap"
                          >
                            <span className="font-medium text-white truncate max-w-[200px]" title={secondary || t.name}>{t.name}</span>
                            <span
                              className={
                                isReadWrite
                                  ? 'inline-flex shrink-0 px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-emerald-500/15 text-emerald-300 border border-emerald-500/40'
                                  : 'inline-flex shrink-0 px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-500/20 text-slate-300 border border-slate-500/35'
                              }
                              title={isReadWrite ? readWriteHint : 'This tool supports read/search only in the platform MCP server (no interactive writes).'}
                            >
                              {isReadWrite ? 'Read & write' : 'Read-only'}
                            </span>
                            {secondary && (
                              <span className="text-white/50 text-xs max-w-[180px] truncate" title={secondary}>{secondary}</span>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  </div>
                </div>
              </div>
            )}
          </>
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
              <p className="text-sm text-white/60 mt-0.5">MCP connections and platform tools available to your agents.</p>
            </div>
            <div className="grid md:grid-cols-2 gap-0 md:gap-6 md:divide-x md:divide-dark-200">
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
            </div>
          </div>
        </div>
      )}

      {view === 'connect' && (
        <ConnectFlow
          connection={editConnection}
          onBack={() => { setView(editConnection ? 'connections' : 'choose'); setEditConnection(null); }}
          onSaved={() => { setView('connections'); setEditConnection(null); loadConnections(); setError(null); }}
          onError={setError}
        />
      )}

      {view === 'connections' && (
        <ConnectionsList
          connections={connections}
          onBack={() => setView('choose')}
          onAdd={() => { setEditConnection(null); setView('connect'); }}
          onEdit={(c) => { setEditConnection(c); setView('connect'); setError(null); }}
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
          onSchemaRefreshed={async (id) => {
            const list = await mcpAPI.listTools()
            setTools(list)
            const updated = list.find((t) => t.id === id)
            if (updated) setEditTool(updated)
          }}
        />
      )}

      {view === 'tools' && (
        <ToolsList
          tools={tools}
          onBack={() => setView('choose')}
          onAdd={() => { setEditTool(null); setView('configure'); setError(null); }}
          onEdit={async (tool) => {
            setError(null)
            try {
              const full = await mcpAPI.getTool(tool.id)
              setEditTool(full)
            } catch (err: unknown) {
              const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
              setError(detail ?? 'Could not load tool details; open the form to re-enter the Chroma URL.')
              setEditTool(tool)
            }
            setView('configure')
          }}
          onRefresh={loadTools}
          onError={setError}
        />
      )}
    </div>
  )
}

function ConnectFlow({
  connection,
  onBack,
  onSaved,
  onError,
}: {
  connection?: MCPServerConnectionRes | null
  onBack: () => void
  onSaved: () => void
  onError: (e: string | null) => void
}) {
  const isEdit = !!connection
  const [name, setName] = useState(connection?.name ?? '')
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? '')
  const [endpointPath, setEndpointPath] = useState(connection?.endpoint_path ?? '/mcp')
  const [authType, setAuthType] = useState(connection?.auth_type ?? 'none')
  const [token, setToken] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [validating, setValidating] = useState(false)
  const [validateMessage, setValidateMessage] = useState<{ success: boolean; text: string } | null>(null)

  useEffect(() => {
    if (connection) {
      setName(connection.name)
      setBaseUrl(connection.base_url)
      setEndpointPath(connection.endpoint_path || '/mcp')
      setAuthType(connection.auth_type || 'none')
    }
  }, [connection])

  const getCredentials = (): Record<string, string> | undefined => {
    if (authType === 'bearer' && token) return { token }
    if (authType === 'api_key' && apiKey) return { api_key: apiKey }
    if (authType === 'basic' && (username || password)) {
      return {
        username: username ?? '',
        password: password ?? '',
      }
    }
    return undefined
  }

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
      const payload: Partial<{
        name: string
        base_url: string
        endpoint_path: string
        auth_type: string
        credentials: Record<string, string>
      }> = {
        name,
        base_url: baseUrl,
        endpoint_path: endpointPath || '/mcp',
        auth_type: authType,
      }
      if (credentials) payload.credentials = credentials

      if (connection) {
        await mcpAPI.updateConnection(connection.id, payload)
      } else {
        await mcpAPI.createConnection(payload as {
          name: string
          base_url: string
          endpoint_path?: string
          auth_type?: string
          credentials?: Record<string, string>
        })
      }
      onSaved()
    } catch (err: unknown) {
      onError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? (isEdit ? 'Failed to update connection' : 'Failed to create connection'))
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
              {submitting ? (isEdit ? 'Saving...' : 'Connecting...') : (isEdit ? 'Save changes' : 'Connect')}
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
  onEdit,
  onRefresh,
  onError,
}: {
  connections: MCPServerConnectionRes[]
  onBack: () => void
  onAdd: () => void
  onEdit: (c: MCPServerConnectionRes) => void
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
                    <>
                      <button type="button" onClick={() => onEdit(c)} disabled={deletingId !== null} className="px-3 py-1.5 rounded-lg border border-dark-200 text-white/80 hover:bg-dark-200/50 text-sm font-medium disabled:opacity-50">
                        Edit
                      </button>
                      <button type="button" onClick={() => handleDeleteClick(c.id)} disabled={deletingId !== null} className="px-3 py-1.5 rounded-lg text-red-400 hover:bg-red-500/20 text-sm font-medium disabled:opacity-50">
                        Delete
                      </button>
                    </>
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
  sqlserver: 'SQL Server',
  snowflake: 'Snowflake',
  databricks: 'Databricks',
  bigquery: 'BigQuery',
  elasticsearch: 'Elasticsearch',
  pageindex: 'PageIndex',
  filesystem: 'File system',
  s3: 'AWS S3',
  minio: 'MinIO',
  ceph: 'Ceph (S3-compatible)',
  azure_blob: 'Azure Blob Storage',
  gcs: 'Google Cloud Storage',
  slack: 'Slack',
  github: 'GitHub',
  notion: 'Notion',
  rest_api: 'REST API',
}

/** Matches backend `_READ_ONLY_PLATFORM_TOOL_TYPES` — used when `access_mode` is absent (stale client/cache). */
const READ_ONLY_PLATFORM_TOOL_TYPES = new Set([
  'vector_db', 'pinecone', 'weaviate', 'qdrant', 'chroma',
  'elasticsearch', 'pageindex', 'github', 'notion',
])

function resolvePlatformRegistryAccessMode(t: {
  access_mode?: 'read_only' | 'read_write'
  tool_type?: string
}): 'read_only' | 'read_write' {
  if (t.access_mode === 'read_only' || t.access_mode === 'read_write') {
    return t.access_mode
  }
  const tt = (t.tool_type || '').toLowerCase()
  if (READ_ONLY_PLATFORM_TOOL_TYPES.has(tt)) return 'read_only'
  return 'read_write'
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
    { value: 'sqlserver', label: 'SQL Server' },
    { value: 'snowflake', label: 'Snowflake' },
    { value: 'databricks', label: 'Databricks' },
    { value: 'bigquery', label: 'BigQuery' },
  ]},
  { label: 'Search', options: [
    { value: 'elasticsearch', label: 'Elasticsearch' },
    { value: 'pageindex', label: 'PageIndex' },
  ]},
  { label: 'Storage', options: [
    { value: 'filesystem', label: 'File system' },
    { value: 's3', label: 'AWS S3' },
    { value: 'minio', label: 'MinIO' },
    { value: 'ceph', label: 'Ceph (S3-compatible)' },
    { value: 'azure_blob', label: 'Azure Blob Storage' },
    { value: 'gcs', label: 'Google Cloud Storage' },
  ]},
  { label: 'Integrations', options: [
    { value: 'slack', label: 'Slack' },
    { value: 'github', label: 'GitHub' },
    { value: 'notion', label: 'Notion' },
    { value: 'rest_api', label: 'REST API' },
  ]},
]

/** True when Chroma URL points at Chroma Cloud (aligns with platform MCP server). */
function isChromaTrycloudUrl(raw: string | undefined): boolean {
  const u = (raw ?? '').trim().toLowerCase()
  if (!u) return false
  try {
    const parsed = u.includes('://') ? new URL(u) : new URL(`https://${u}`)
    const h = parsed.hostname.toLowerCase()
    return h === 'trychroma.com' || h.endsWith('.trychroma.com')
  } catch {
    // Unparseable input must not match via substring (CodeQL: incomplete URL substring sanitization).
    return false
  }
}

function ConfigureFlow({
  editTool,
  onBack,
  onSaved,
  onError,
  onSchemaRefreshed,
}: {
  editTool: MCPToolConfigRes | null
  onBack: () => void
  onSaved: () => void
  onError: (e: string | null) => void
  onSchemaRefreshed?: (toolId: number) => Promise<void>
}) {
  const [toolType, setToolType] = useState(editTool?.tool_type ?? 'vector_db')
  const [name, setName] = useState(editTool?.name ?? '')
  const [businessDescription, setBusinessDescription] = useState(editTool?.business_description ?? '')
  const [config, setConfig] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [validating, setValidating] = useState(false)
  const [refreshingSchema, setRefreshingSchema] = useState(false)
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
      setBusinessDescription(editTool.business_description ?? '')
      const init: Record<string, string> = {}
      if (editTool.tool_type === 'chroma' && editTool.chroma_url_preview?.trim()) {
        init.url = editTool.chroma_url_preview.trim()
      }
      if (editTool.tool_type === 'weaviate') {
        if (editTool.weaviate_cluster_preview?.trim()) {
          init.weaviate_cluster_name = editTool.weaviate_cluster_preview.trim()
        }
        if (editTool.weaviate_class_preview?.trim()) {
          init.index_name = editTool.weaviate_class_preview.trim()
        }
      }
      setConfig(init)
    } else {
      setName('')
      setBusinessDescription('')
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
      { key: 'openai_api_key', label: 'OpenAI API key (optional, for embedding)', placeholder: 'Only if index has no integrated embedding', secret: true },
      { key: 'embedding_model', label: 'Embedding model (optional)', placeholder: 'text-embedding-3-small' },
    ],
    weaviate: [
      {
        key: 'api_key',
        label: 'Weaviate API key',
        placeholder: 'Required for Weaviate Cloud (*.weaviate.cloud); optional for local Docker',
        secret: true,
      },
      {
        key: 'url',
        label: 'Weaviate URL',
        placeholder:
          'Docker: http://host.docker.internal:8080 if Weaviate runs on host; or https://xxx.weaviate.cloud for WCD',
      },
      {
        key: 'weaviate_cluster_name',
        label: 'Cluster name (Weaviate Cloud)',
        placeholder:
          'Optional: WCD sandbox/display name (e.g. rahul). Does not replace collection name — for your reference and agent context only.',
      },
      {
        key: 'index_name',
        label: 'Collection / class name',
        placeholder: 'Exact Weaviate class, e.g. SampleWebsites (from console or GET /v1/schema)',
      },
      {
        key: 'weaviate_skip_init_checks',
        label: 'Skip startup readiness check (optional)',
        placeholder: 'true — if queries fail with WeaviateStartUpError from this server',
        secret: false,
      },
      {
        key: 'weaviate_init_timeout_seconds',
        label: 'Connection init timeout seconds (optional)',
        placeholder: 'Default 45 — increase if Docker/cloud is slow (max 180)',
        secret: false,
      },
      {
        key: 'weaviate_trust_env',
        label: 'Trust HTTP_PROXY from environment (optional)',
        placeholder: 'true — if Weaviate is reached via corporate proxy',
        secret: false,
      },
      { key: 'openai_api_key', label: 'OpenAI API key (optional, for embedding)', placeholder: 'If collection has no vectorizer', secret: true },
      { key: 'embedding_model', label: 'Embedding model (optional)', placeholder: 'text-embedding-3-small' },
    ],
    qdrant: [
      { key: 'api_key', label: 'Qdrant API key', placeholder: 'Required for Qdrant Cloud', secret: true },
      { key: 'url', label: 'Qdrant URL', placeholder: 'https://xxx.cloud.qdrant.io:6333 or http://localhost:6333' },
      { key: 'index_name', label: 'Collection name', placeholder: 'my_collection' },
      { key: 'openai_api_key', label: 'OpenAI API key (optional)', placeholder: 'Only for self-hosted Qdrant', secret: true },
      { key: 'embedding_model', label: 'Model (must match collection)', placeholder: 'e.g. sentence-transformers/all-minilm-l6-v2 or intfloat/multilingual-e5-small', secret: false },
    ],
    chroma: [
      {
        key: 'url',
        label: 'Chroma URL',
        placeholder:
          'Self-hosted: http://host:8000 (HTTPS respected). Chroma Cloud: use host api.trychroma.com (HTTPS) so the platform uses CloudClient.',
        secret: false,
      },
      {
        key: 'api_key',
        label: 'Chroma API key',
        placeholder: 'Chroma Cloud key, or self-hosted X-Chroma-Token if auth is enabled',
        secret: true,
      },
      { key: 'tenant', label: 'Tenant ID (Chroma Cloud)', placeholder: 'e.g. c555ee60-feab-4407-82b2-c272fcf9fbd0', secret: false },
      { key: 'database', label: 'Database name (Chroma Cloud)', placeholder: 'e.g. dev', secret: false },
      {
        key: 'index_name',
        label: 'Collection name',
        placeholder: 'Exact name from Chroma UI, e.g. customer-support-messages',
        secret: false,
      },
      {
        key: 'openai_api_key',
        label: 'OpenAI API key (self-hosted fallback only)',
        placeholder:
          'Chroma Cloud: usually leave empty (dashboard uses server models like Qwen). Self-hosted without query embedding: add key here.',
        secret: true,
      },
      {
        key: 'embedding_model',
        label: 'Embedding model (optional)',
        placeholder:
          'Self-hosted + OpenAI fallback only (e.g. text-embedding-3-small). Not used for Chroma Cloud HTTP embed.',
        secret: false,
      },
      {
        key: 'chroma_embed_model',
        label: 'Chroma Cloud embed model id (optional)',
        placeholder:
          'HuggingFace id from Chroma UI only, e.g. Qwen/Qwen3-Embedding-0.6B — not OpenAI model names',
        secret: false,
      },
    ],
    postgres: [
      { key: 'connection_string', label: 'Connection string', placeholder: 'postgresql://user:pass@host:5432/db', secret: true },
      { key: 'schema', label: 'Schema (optional)', placeholder: 'public' },
      { key: 'query', label: 'Default SQL query (optional fallback)', placeholder: 'SELECT now()' },
    ],
    mysql: [
      { key: 'host', label: 'Host', placeholder: 'localhost' },
      { key: 'port', label: 'Port', placeholder: '3306' },
      { key: 'user', label: 'User', placeholder: 'root', secret: false },
      { key: 'password', label: 'Password', placeholder: '', secret: true },
      { key: 'database', label: 'Database', placeholder: 'mydb' },
      { key: 'ssl_mode', label: 'SSL mode (optional)', placeholder: 'required | preferred | verify_ca | verify_identity | disabled' },
      { key: 'ssl_ca', label: 'SSL CA path (optional)', placeholder: '/app/certs/ca.pem' },
      { key: 'ssl_cert', label: 'Client cert path (optional)', placeholder: '/app/certs/client-cert.pem' },
      { key: 'ssl_key', label: 'Client key path (optional)', placeholder: '/app/certs/client-key.pem', secret: true },
      { key: 'query', label: 'Default SQL query (optional fallback)', placeholder: 'SELECT NOW()' },
    ],
    sqlserver: [
      { key: 'host', label: 'Host', placeholder: 'sql.example.com' },
      { key: 'port', label: 'Port', placeholder: '1433' },
      { key: 'database', label: 'Database', placeholder: 'mydb' },
      { key: 'user', label: 'User', placeholder: 'app_user', secret: false },
      { key: 'password', label: 'Password', placeholder: '', secret: true },
      { key: 'query', label: 'Default SQL query (optional fallback)', placeholder: 'SELECT GETUTCDATE()' },
    ],
    snowflake: [
      { key: 'account', label: 'Account identifier', placeholder: 'xy12345.us-east-1.aws' },
      { key: 'role', label: 'Role (optional)', placeholder: 'ACCOUNTADMIN' },
      { key: 'user', label: 'User', placeholder: 'SERVICE_USER' },
      { key: 'password', label: 'Password', placeholder: '', secret: true },
      { key: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH' },
      { key: 'database', label: 'Database', placeholder: 'MY_DB' },
      { key: 'schema', label: 'Schema', placeholder: 'PUBLIC' },
      { key: 'query', label: 'Default SQL query (optional fallback)', placeholder: 'SELECT CURRENT_TIMESTAMP()' },
    ],
    databricks: [
      { key: 'host', label: 'Workspace URL', placeholder: 'https://dbc-xxxx.cloud.databricks.com' },
      { key: 'sql_warehouse_id', label: 'SQL warehouse ID', placeholder: 'Required for SQL queries' },
      { key: 'token', label: 'Personal access token', placeholder: 'dapi...', secret: true },
      { key: 'query', label: 'Default SQL query (optional fallback)', placeholder: 'SELECT current_timestamp()' },
    ],
    bigquery: [
      { key: 'project_id', label: 'GCP project ID', placeholder: 'my-project' },
      { key: 'dataset', label: 'Dataset', placeholder: 'my_dataset' },
      { key: 'credentials_json', label: 'Service account JSON (optional)', placeholder: 'Paste JSON if not using ADC', secret: true },
      { key: 'query', label: 'Default SQL query (optional fallback)', placeholder: 'SELECT CURRENT_TIMESTAMP()' },
    ],
    elasticsearch: [
      { key: 'url', label: 'Elasticsearch URL', placeholder: 'http://localhost:9200' },
      { key: 'api_key', label: 'API key (optional)', placeholder: '', secret: true },
    ],
    pageindex: [
      { key: 'api_key', label: 'PageIndex API key', placeholder: 'From https://dash.pageindex.ai', secret: true },
      { key: 'base_url', label: 'API base URL (optional)', placeholder: 'https://api.pageindex.ai' },
      { key: 'default_doc_id', label: 'Default document ID (optional)', placeholder: 'e.g. pi-abc123' },
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
    minio: [
      { key: 'endpoint', label: 'Endpoint URL', placeholder: 'http://minio:9000' },
      { key: 'bucket', label: 'Bucket name', placeholder: 'my-bucket' },
      { key: 'access_key', label: 'Access key (optional)', placeholder: 'minioadmin', secret: true },
      { key: 'secret_key', label: 'Secret key (optional)', placeholder: '', secret: true },
    ],
    ceph: [
      { key: 'endpoint', label: 'RGW / S3 endpoint URL', placeholder: 'http://ceph-rgw:7480' },
      { key: 'bucket', label: 'Bucket name', placeholder: 'my-bucket' },
      { key: 'access_key', label: 'Access key (optional)', placeholder: '', secret: true },
      { key: 'secret_key', label: 'Secret key (optional)', placeholder: '', secret: true },
    ],
    azure_blob: [
      { key: 'account_url', label: 'Account URL', placeholder: 'https://myaccount.blob.core.windows.net' },
      { key: 'container', label: 'Container name', placeholder: 'my-container' },
      { key: 'connection_string', label: 'Connection string (optional)', placeholder: 'DefaultEndpointsProtocol=...', secret: true },
    ],
    gcs: [
      { key: 'project_id', label: 'GCP project ID', placeholder: 'my-project' },
      { key: 'bucket', label: 'Bucket name', placeholder: 'my-bucket' },
      { key: 'credentials_json', label: 'Service account JSON (optional)', placeholder: 'Paste JSON if not using ADC', secret: true },
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

  const baseFields = configFields[toolType] ?? configFields.vector_db
  const visibleFields =
    toolType === 'chroma'
      ? baseFields.filter(
          (f) =>
            !isChromaTrycloudUrl(config.url) ||
            (f.key !== 'openai_api_key' && f.key !== 'embedding_model'),
        )
      : baseFields

  const buildConfigPayload = (): Record<string, unknown> => {
    const payload: Record<string, unknown> = {}
    visibleFields.forEach((f) => {
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
      if (toolType === 'sqlserver' && (!config.host?.trim() || !config.database?.trim())) {
        setValidateMessage({ success: false, text: 'SQL Server requires host and database.' })
        return
      }
      if (toolType === 'snowflake' && (!config.account?.trim() || !config.warehouse?.trim())) {
        setValidateMessage({ success: false, text: 'Snowflake requires account and warehouse.' })
        return
      }
      if (toolType === 'databricks' && (!config.host?.trim() || !config.sql_warehouse_id?.trim())) {
        setValidateMessage({ success: false, text: 'Databricks requires workspace URL and SQL warehouse ID.' })
        return
      }
      if (toolType === 'bigquery' && (!config.project_id?.trim() || !config.dataset?.trim())) {
        setValidateMessage({ success: false, text: 'BigQuery requires project ID and dataset.' })
        return
      }
      if (toolType === 'pageindex' && !config.api_key?.trim()) {
        setValidateMessage({ success: false, text: 'PageIndex requires an API key.' })
        return
      }
      if (toolType === 'minio' && (!config.endpoint?.trim() || !config.bucket?.trim())) {
        setValidateMessage({ success: false, text: 'MinIO requires endpoint and bucket.' })
        return
      }
      if (toolType === 'ceph' && (!config.endpoint?.trim() || !config.bucket?.trim())) {
        setValidateMessage({ success: false, text: 'Ceph requires endpoint and bucket.' })
        return
      }
      if (toolType === 'azure_blob' && (!config.account_url?.trim() || !config.container?.trim())) {
        setValidateMessage({ success: false, text: 'Azure Blob requires account URL and container.' })
        return
      }
      if (toolType === 'gcs' && (!config.project_id?.trim() || !config.bucket?.trim())) {
        setValidateMessage({ success: false, text: 'Google Cloud Storage requires project ID and bucket.' })
        return
      }
    }
    const configPayload = buildConfigPayload()
    setSubmitting(true)
    try {
      const businessDesc = businessDescription.trim() || undefined
      if (editTool) {
        await mcpAPI.updateTool(editTool.id, { name, business_description: businessDesc ?? null, ...(Object.keys(configPayload).length > 0 ? { config: configPayload } : {}) })
      } else {
        await mcpAPI.createTool({ tool_type: toolType, name, config: configPayload, business_description: businessDesc ?? undefined })
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
          <div>
            <label className="block text-sm font-medium text-white/80 mb-1">Business description (optional)</label>
            <textarea
              value={businessDescription}
              onChange={(e) => setBusinessDescription(e.target.value)}
              maxLength={2000}
              rows={2}
              className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500 resize-y"
              placeholder="e.g. Sales DB: orders, customers, products. Helps the agent write correct SQL."
            />
            <p className="mt-1 text-xs text-white/50">Short context for the agent (e.g. what tables mean). Shown with schema so the agent can write correct queries.</p>
          </div>
          {visibleFields.map((f) => (
            <div key={f.key}>
              <label className="block text-sm font-medium text-white/80 mb-1">{f.label}</label>
              <input
                type={f.secret ? 'password' : 'text'}
                value={config[f.key] ?? ''}
                onChange={(e) => setConfig((prev) => ({ ...prev, [f.key]: e.target.value }))}
                className="w-full px-4 py-2.5 rounded-xl bg-dark-50 border border-dark-200 text-white placeholder-white/40 focus:ring-2 focus:ring-primary-500"
                placeholder={editTool && f.secret ? 'Leave blank to keep existing' : f.placeholder}
              />
              {toolType === 'chroma' && f.key === 'url' && isChromaTrycloudUrl(config.url) && (
                <p className="mt-1 text-xs text-white/50">
                  Chroma Cloud: query embedding uses Chroma&apos;s models (e.g. Qwen). OpenAI key and OpenAI embedding model fields are hidden. Use &quot;Chroma Cloud embed model id&quot; below only if your dashboard shows a different HuggingFace id.
                </p>
              )}
              {toolType === 'postgres' && f.key === 'connection_string' && (
                <p className="mt-1 text-xs text-white/50">If the app runs in Docker, use <code className="bg-dark-200/50 px-1 rounded">host.docker.internal</code> instead of localhost to reach PostgreSQL on your host.</p>
              )}
              {toolType === 'mysql' && f.key === 'ssl_mode' && (
                <p className="mt-1 text-xs text-white/50">
                  If your provider enforces secure transport (for example Azure MySQL Flexible Server), set <code className="bg-dark-200/50 px-1 rounded">required</code>.
                </p>
              )}
              {toolType === 'mysql' && f.key === 'ssl_ca' && (
                <p className="mt-1 text-xs text-white/50">
                  Usually optional. Leave SSL cert/key fields empty for standard TLS; only set CA/cert/key paths when your provider requires custom certificate verification.
                </p>
              )}
              {['postgres', 'mysql', 'sqlserver', 'snowflake', 'databricks', 'bigquery'].includes(toolType) && f.key === 'query' && (
                <p className="mt-1 text-xs text-white/50">
                  Optional fallback query. Agents can provide per-job runtime read-only SELECT/WITH query; this value is used when runtime query is not supplied.
                </p>
              )}
            </div>
          ))}
          {validateMessage && (
            <div className={`p-3 rounded-xl text-sm ${validateMessage.success ? 'bg-green-500/20 border border-green-500/50 text-green-200' : 'bg-amber-500/20 border border-amber-500/50 text-amber-200'}`}>
              {validateMessage.text}
            </div>
          )}
          {(editTool && (toolType === 'postgres' || toolType === 'mysql' || toolType === 'sqlserver')) && (
            <div className="p-3 rounded-xl bg-dark-50 border border-dark-200">
              <p className="text-sm text-white/80 mb-2">Load database schema so the agent can write correct SQL. Refresh after changing the connection.</p>
              <button
                type="button"
                onClick={async () => {
                  if (!editTool) return
                  setValidateMessage(null)
                  setRefreshingSchema(true)
                  onError(null)
                  try {
                    const res = await mcpAPI.refreshToolSchema(editTool.id)
                    setValidateMessage({ success: true, text: res.message })
                    await onSchemaRefreshed?.(editTool.id)
                  } catch (err: unknown) {
                    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
                    setValidateMessage({ success: false, text: detail ?? 'Failed to refresh schema' })
                  } finally {
                    setRefreshingSchema(false)
                  }
                }}
                disabled={refreshingSchema}
                className="px-4 py-2 rounded-lg border border-primary-500/70 text-primary-400 hover:bg-primary-500/20 disabled:opacity-50 text-sm font-medium"
              >
                {refreshingSchema ? 'Refreshing schema...' : 'Refresh schema'}
              </button>
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
                    {(t.tool_type === 'postgres' || t.tool_type === 'mysql' || t.tool_type === 'sqlserver') && (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-dark-200/80 text-white/70 border border-dark-200">
                        {t.schema_table_count != null ? `Schema: ${t.schema_table_count} table${t.schema_table_count !== 1 ? 's' : ''}` : 'Schema: not loaded'}
                      </span>
                    )}
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
