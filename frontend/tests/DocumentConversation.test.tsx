import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { DocumentConversation } from '../src/components/DocumentConversation'

const mockGenerateWorkflowQuestions = vi.fn()

vi.mock('../src/lib/api', () => ({
  jobsAPI: {
    answerQuestion: vi.fn(),
    generateWorkflowQuestions: (...args: any[]) => mockGenerateWorkflowQuestions(...args),
  },
}))

describe('DocumentConversation clarification flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('auto-advances when no clarification questions are needed', async () => {
    const onNoClarificationNeeded = vi.fn()
    const onConversationUpdate = vi.fn()
    mockGenerateWorkflowQuestions.mockResolvedValue({
      questions: [],
      added_questions: [],
      no_questions_needed: true,
      conversation: [{ type: 'analysis', content: 'Looks complete' }],
    })

    render(
      <DocumentConversation
        jobId={123}
        files={[{ id: 'f1', name: 'brd.docx' }]}
        workflowSteps={[{ step_order: 1, agent_name: 'Agent 1' }]}
        initialConversation={[]}
        onNoClarificationNeeded={onNoClarificationNeeded}
        onConversationUpdate={onConversationUpdate}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Generate clarification questions/i }))

    await waitFor(() => {
      expect(mockGenerateWorkflowQuestions).toHaveBeenCalledWith(123)
    })
    await waitFor(() => {
      expect(onNoClarificationNeeded).toHaveBeenCalledTimes(1)
    })
    expect(onConversationUpdate).toHaveBeenCalled()
    expect(screen.getByText(/No clarification questions needed/i)).toBeInTheDocument()
  })

  it('stays on Q&A and shows count when new questions are generated', async () => {
    const onNoClarificationNeeded = vi.fn()
    mockGenerateWorkflowQuestions.mockResolvedValue({
      questions: ['What is the target number?', 'Should we round the result?'],
      added_questions: ['What is the target number?', 'Should we round the result?'],
      no_questions_needed: false,
      conversation: [
        { type: 'question', question: 'What is the target number?', answer: null },
        { type: 'question', question: 'Should we round the result?', answer: null },
      ],
    })

    render(
      <DocumentConversation
        jobId={456}
        files={[{ id: 'f1', name: 'brd.docx' }]}
        workflowSteps={[{ step_order: 1, agent_name: 'Agent 1' }]}
        initialConversation={[]}
        onNoClarificationNeeded={onNoClarificationNeeded}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Generate clarification questions/i }))

    await waitFor(() => {
      expect(mockGenerateWorkflowQuestions).toHaveBeenCalledWith(456)
    })
    await waitFor(() => {
      expect(screen.getByText(/2 clarification questions generated/i)).toBeInTheDocument()
    })
    expect(onNoClarificationNeeded).not.toHaveBeenCalled()
  })

  it('auto-advances when API returns questions but conversation has no unanswered items', async () => {
    const onNoClarificationNeeded = vi.fn()
    // Simulate backend/model returning non-empty raw questions, but after dedupe
    // there are no unanswered questions in the final conversation.
    mockGenerateWorkflowQuestions.mockResolvedValue({
      questions: ['What is the target number?'],
      conversation: [
        { type: 'analysis', content: 'The sum of 5 and 7 is 12' },
        { type: 'completion', message: 'Requirements understood. Here are the solutions:' },
      ],
    })

    render(
      <DocumentConversation
        jobId={789}
        files={[{ id: 'f1', name: 'brd.docx' }]}
        workflowSteps={[{ step_order: 1, agent_name: 'Agent 1' }]}
        initialConversation={[]}
        onNoClarificationNeeded={onNoClarificationNeeded}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Generate clarification questions/i }))

    await waitFor(() => {
      expect(mockGenerateWorkflowQuestions).toHaveBeenCalledWith(789)
    })
    await waitFor(() => {
      expect(onNoClarificationNeeded).toHaveBeenCalledTimes(1)
    })
    expect(screen.getByText(/No clarification questions needed/i)).toBeInTheDocument()
  })
})

