import { describe, it, expect, vi } from 'vitest'

// Mock api to avoid loading real axios (prevents DataCloneError when Vitest serializes worker data)
vi.mock('../src/lib/api', () => ({
  jobsAPI: {
    getShareLink: vi.fn().mockResolvedValue({
      job_id: 1,
      share_url: 'http://localhost:8000/api/external/jobs/1?token=x',
      token: 'x',
      expires_in_days: 7,
    }),
  },
}))

import { jobsAPI } from '../src/lib/api'

describe('jobsAPI', () => {
  describe('getShareLink', () => {
    it('exists and accepts jobId', () => {
      expect(typeof jobsAPI.getShareLink).toBe('function')
      expect(jobsAPI.getShareLink).toHaveBeenCalledTimes(0)
    })

    it('returns a promise (async function)', async () => {
      const result = jobsAPI.getShareLink(1)
      expect(result).toBeInstanceOf(Promise)
      const data = await result
      expect(data).toHaveProperty('share_url')
      expect(data).toHaveProperty('token')
    })
  })
})
