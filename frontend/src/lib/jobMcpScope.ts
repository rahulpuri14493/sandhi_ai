/**
 * Job-level MCP allowlists from job creation. When both are empty/unset, the UI
 * treats the job as having no explicit tool scope (per-step empty = show empty).
 */

export function jobHasExplicitMcpScope(job: {
  allowed_platform_tool_ids?: number[] | null
  allowed_connection_ids?: number[] | null
} | null | undefined): boolean {
  return (
    (job?.allowed_platform_tool_ids?.length ?? 0) > 0 ||
    (job?.allowed_connection_ids?.length ?? 0) > 0
  )
}

/**
 * Restrict dropdowns to the job’s allowlist from creation (or edit).
 * `null` / `undefined` means no job-level list was stored — show the full catalog.
 * An empty array `[]` is explicit (e.g. only connections selected) — show no items for that side.
 */
export function filterByJobAllowedIds<T extends { id: number }>(
  items: T[],
  allowedIds: number[] | null | undefined
): T[] {
  if (allowedIds == null) return items
  return items.filter((item) => allowedIds.includes(item.id))
}
