/**
 * UI labels aligned with backend/services/mcp_tool_capabilities.py
 * for BRD-aligned tool planning (read vs write vs contract).
 */
export type ToolAccessBadge = {
  label: string
  short: 'search' | 'sql' | 'object' | 'messaging' | 'integration' | 'mixed'
  hint: string
}

export function getToolAccessBadge(toolType: string | undefined | null): ToolAccessBadge {
  const t = String(toolType || '')
    .trim()
    .toLowerCase()
  const search = new Set([
    'vector_db',
    'pinecone',
    'weaviate',
    'qdrant',
    'chroma',
    'elasticsearch',
    'pageindex',
  ])
  const sql = new Set(['postgres', 'mysql', 'sqlserver', 'snowflake', 'databricks', 'bigquery'])
  const object = new Set(['s3', 'minio', 'ceph', 'azure_blob', 'gcs', 'filesystem'])
  const messaging = new Set(['slack'])
  const integration = new Set(['github', 'notion'])

  if (search.has(t)) {
    return {
      short: 'search',
      label: 'Search / retrieve',
      hint: 'Use in early steps for evidence and RAG; least privilege in prompts.',
    }
  }
  if (sql.has(t)) {
    return {
      short: 'sql',
      label: 'SQL (read + write)',
      hint: 'Interactive query vs DML is your SQL + DB policy; use output contract for bulk artifact loads.',
    }
  }
  if (object.has(t)) {
    return {
      short: 'object',
      label: 'Files / object storage',
      hint: 'Interactive list/get/put; platform write mode copies step artifacts to bucket/prefix.',
    }
  }
  if (messaging.has(t)) {
    return {
      short: 'messaging',
      label: 'Messaging',
      hint: 'Side effects — assign only where needed.',
    }
  }
  if (integration.has(t)) {
    return {
      short: 'integration',
      label: 'Read-mostly API',
      hint: 'Typically fetch/search; not used for artifact contract writes.',
    }
  }
  if (t === 'rest_api') {
    return {
      short: 'mixed',
      label: 'REST API',
      hint: 'Read/write depends on routes; scope per step.',
    }
  }
  return {
    short: 'mixed',
    label: 'Tool',
    hint: 'See tool description and output contract for writes.',
  }
}
