import { describe, it, expect } from 'vitest'
import { cn } from '@/lib/utils'
import { getToolAccessBadge } from '@/lib/mcpToolAccess'
import { filterByJobAllowedIds, jobHasExplicitMcpScope } from '@/lib/jobMcpScope'

describe('cn', () => {
  it('merges class names', () => {
    expect(cn('a', 'b')).toBeTruthy()
    expect(cn('px-2', false && 'hidden', 'py-1')).toContain('px-2')
  })
})

describe('getToolAccessBadge', () => {
  it('returns search for vector backends', () => {
    const b = getToolAccessBadge('pinecone')
    expect(b.short).toBe('search')
    expect(b.label).toContain('Search')
  })

  it('returns sql for database tools', () => {
    const b = getToolAccessBadge('postgres')
    expect(b.short).toBe('sql')
  })

  it('returns object for storage tools', () => {
    expect(getToolAccessBadge('s3').short).toBe('object')
    expect(getToolAccessBadge('minio').short).toBe('object')
  })

  it('returns messaging read+write for slack, teams, smtp', () => {
    for (const tt of ['slack', 'teams', 'smtp']) {
      const b = getToolAccessBadge(tt)
      expect(b.short).toBe('messaging')
      expect(b.label).toContain('read')
      expect(b.label).toContain('write')
    }
  })

  it('returns integration for github', () => {
    expect(getToolAccessBadge('github').short).toBe('integration')
  })

  it('handles rest_api', () => {
    expect(getToolAccessBadge('rest_api').short).toBe('mixed')
  })

  it('defaults unknown types to mixed', () => {
    const b = getToolAccessBadge('unknown_tool_xyz')
    expect(b.short).toBe('mixed')
    expect(b.label).toBe('Tool')
  })

  it('trims and lowercases type', () => {
    expect(getToolAccessBadge('  POSTGRES  ').short).toBe('sql')
  })
})

describe('jobHasExplicitMcpScope', () => {
  it('is false when job is null or lists are empty', () => {
    expect(jobHasExplicitMcpScope(null)).toBe(false)
    expect(jobHasExplicitMcpScope(undefined)).toBe(false)
    expect(jobHasExplicitMcpScope({})).toBe(false)
    expect(jobHasExplicitMcpScope({ allowed_platform_tool_ids: [], allowed_connection_ids: [] })).toBe(false)
  })

  it('is true when either allowlist is non-empty', () => {
    expect(jobHasExplicitMcpScope({ allowed_platform_tool_ids: [1] })).toBe(true)
    expect(jobHasExplicitMcpScope({ allowed_connection_ids: [2] })).toBe(true)
  })
})

describe('filterByJobAllowedIds', () => {
  const items = [{ id: 1 }, { id: 2 }, { id: 3 }]

  it('returns all items when allowlist is null or undefined', () => {
    expect(filterByJobAllowedIds(items, null)).toEqual(items)
    expect(filterByJobAllowedIds(items, undefined)).toEqual(items)
  })

  it('returns empty when allowlist is an empty array', () => {
    expect(filterByJobAllowedIds(items, [])).toEqual([])
  })

  it('returns only matching ids', () => {
    expect(filterByJobAllowedIds(items, [2])).toEqual([{ id: 2 }])
  })
})
