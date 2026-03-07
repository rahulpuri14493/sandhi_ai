import { describe, it, expect, vi, beforeEach } from 'vitest'

// vi.mock is hoisted; mocks must be created in vi.hoisted so they exist when the factory runs
const { mockGet, mockPost, mockPut, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockPut: vi.fn(),
  mockDelete: vi.fn(),
}))

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: mockGet,
      post: mockPost,
      put: mockPut,
      delete: mockDelete,
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}))

import { agentsAPI } from '../src/lib/api'

describe('agentsAPI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue({ data: {} })
    mockPost.mockResolvedValue({ data: {} })
    mockPut.mockResolvedValue({ data: {} })
    mockDelete.mockResolvedValue(undefined)
  })

  it('exposes list, get, create, update, delete', () => {
    expect(typeof agentsAPI.list).toBe('function')
    expect(typeof agentsAPI.get).toBe('function')
    expect(typeof agentsAPI.create).toBe('function')
    expect(typeof agentsAPI.update).toBe('function')
    expect(typeof agentsAPI.delete).toBe('function')
  })

  it('exposes review methods: getReviewSummary, listReviews, submitReview, updateReview, deleteReview', () => {
    expect(typeof agentsAPI.getReviewSummary).toBe('function')
    expect(typeof agentsAPI.listReviews).toBe('function')
    expect(typeof agentsAPI.submitReview).toBe('function')
    expect(typeof agentsAPI.updateReview).toBe('function')
    expect(typeof agentsAPI.deleteReview).toBe('function')
  })

  it('getReviewSummary calls GET /agents/:id/reviews/summary', async () => {
    await agentsAPI.getReviewSummary(5)
    expect(mockGet).toHaveBeenCalledWith('/agents/5/reviews/summary')
  })

  it('listReviews calls GET /agents/:id/reviews with limit and offset', async () => {
    await agentsAPI.listReviews(3, 20, 0)
    expect(mockGet).toHaveBeenCalledWith('/agents/3/reviews?limit=20&offset=0')
  })

  it('submitReview calls POST /agents/:id/reviews with rating and review_text', async () => {
    await agentsAPI.submitReview(1, 5, 'Great agent')
    expect(mockPost).toHaveBeenCalledWith('/agents/1/reviews', { rating: 5, review_text: 'Great agent' })
  })

  it('submitReview with rating only sends rating and review_text', async () => {
    await agentsAPI.submitReview(1, 4)
    expect(mockPost).toHaveBeenCalledWith('/agents/1/reviews', expect.objectContaining({ rating: 4 }))
  })

  it('updateReview calls PUT /agents/:id/reviews/:reviewId', async () => {
    await agentsAPI.updateReview(1, 10, { rating: 5, review_text: 'Updated' })
    expect(mockPut).toHaveBeenCalledWith('/agents/1/reviews/10', { rating: 5, review_text: 'Updated' })
  })

  it('deleteReview calls DELETE /agents/:id/reviews/:reviewId', async () => {
    await agentsAPI.deleteReview(1, 10)
    expect(mockDelete).toHaveBeenCalledWith('/agents/1/reviews/10')
  })
})
