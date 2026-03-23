import { describe, it, expect } from 'vitest'
import { cn } from '@/lib/utils'
import { getToolAccessBadge } from '@/lib/mcpToolAccess'

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

  it('returns messaging for slack', () => {
    expect(getToolAccessBadge('slack').short).toBe('messaging')
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
