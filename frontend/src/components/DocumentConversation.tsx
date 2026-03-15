import { useState, useEffect } from 'react'
import { jobsAPI } from '../lib/api'
import type { ConversationItem, WorkflowCollaborationHint } from '../lib/types'

interface DocumentConversationProps {
  jobId: number
  files?: Array<{ id: string; name: string }>
  initialConversation?: ConversationItem[]
  onConversationUpdate?: (conversation: ConversationItem[]) => void
  /** When present (workflow already built), show "Generate clarification questions from workflow & BRD" */
  workflowSteps?: Array<{ step_order: number; agent_name?: string }>
}

export function DocumentConversation({ jobId, files, initialConversation = [], onConversationUpdate, workflowSteps }: DocumentConversationProps) {
  const [conversation, setConversation] = useState<ConversationItem[]>(initialConversation)
  const [currentAnswer, setCurrentAnswer] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isGeneratingWorkflowQuestions, setIsGeneratingWorkflowQuestions] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setConversation(initialConversation)
  }, [initialConversation])

  const handleSubmitAnswer = async () => {
    if (!currentAnswer.trim()) {
      setError('Please provide an answer')
      return
    }

    setIsSubmitting(true)
    setError('')
    try {
      const result = await jobsAPI.answerQuestion(jobId, currentAnswer.trim())
      // Update conversation state with the latest from server
      const updatedConversation = result.conversation || []
      setConversation(updatedConversation)
      setCurrentAnswer('')
      
      if (onConversationUpdate) {
        onConversationUpdate(updatedConversation)
      }
      
      // Check if all questions are answered or completion message exists
      const hasUnanswered = updatedConversation.some((item: ConversationItem) =>
        item.type === 'question' && (!item.answer || item.answer.trim() === '')
      )
      const hasCompletion = updatedConversation.some((item: ConversationItem) => item.type === 'completion')
      
      if (!hasUnanswered || hasCompletion) {
        // All questions answered or completion received
        if (hasCompletion) {
          // Completion message will be shown in the conversation
        } else {
          // Show success message
          console.log('All questions answered successfully')
        }
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to submit answer')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleGenerateWorkflowQuestions = async () => {
    setIsGeneratingWorkflowQuestions(true)
    setError('')
    try {
      const result = await jobsAPI.generateWorkflowQuestions(jobId)
      const updated = result.conversation || []
      setConversation(updated)
      if (onConversationUpdate) onConversationUpdate(updated)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to generate workflow questions')
    } finally {
      setIsGeneratingWorkflowQuestions(false)
    }
  }

  const getUnansweredQuestion = (): ConversationItem | null => {
    // Question texts we've already seen answered (so we skip duplicate question items)
    const answeredTexts = new Set(
      conversation
        .filter(item => item.type === 'question' && item.answer && String(item.answer).trim())
        .map(item => String(item.question || '').trim())
    )
    // First unanswered question whose text we haven't already answered elsewhere
    return conversation.find(item => {
      if (item.type !== 'question' || !item.question) return false
      if (item.answer && String(item.answer).trim()) return false
      const text = String(item.question).trim()
      return !answeredTexts.has(text)
    }) as ConversationItem | null || null
  }

  const unansweredQuestion = getUnansweredQuestion()
  const hasQuestions = conversation.some(item => item.type === 'question')
  const hasAnalysis = conversation.some(item => item.type === 'analysis')
  const hasCompletion = conversation.some(item => item.type === 'completion')
  const completionWithHint = conversation.find(
    (item): item is ConversationItem & { workflow_collaboration_hint: WorkflowCollaborationHint; workflow_collaboration_reason?: string | null } =>
      item.type === 'completion' && !!item.workflow_collaboration_hint
  )

  return (
    <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
      <div className="mb-4">
        <h2 className="text-4xl font-black text-white tracking-tight">Document Analysis & Q&A</h2>
        <p className="mt-2 text-sm text-white/70 font-medium">
          Order: Use <strong className="text-white/90">Analyze Documents</strong> on the Job screen (above) (optional) → <strong className="text-white/90">Build Workflow</strong> → then use the button below to <strong className="text-white/90">Generate clarification questions</strong> (optional) → answer questions below → Preview Cost → Execute.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3 mb-6">
        {workflowSteps && workflowSteps.length > 0 && (
          <button
            onClick={handleGenerateWorkflowQuestions}
            disabled={isGeneratingWorkflowQuestions}
            className="px-6 py-3 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
          >
            {isGeneratingWorkflowQuestions ? (
              <span className="flex items-center gap-2">
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                Generating...
              </span>
            ) : (
              'Generate clarification questions (workflow + BRD) (optional)'
            )}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/20 border-2 border-red-500/50 text-red-400 rounded-xl font-semibold">
          {error}
        </div>
      )}

      {(hasAnalysis || hasCompletion) && (
        <div className="mb-6 p-5 bg-primary-500/10 border-2 border-primary-500/30 rounded-xl">
          <h3 className="font-bold text-primary-400 mb-2 text-base">Understanding workflow modes</h3>
          <p className="text-sm text-white/70 font-medium mb-2">
            All agents are invoked over the A2A protocol by the platform (OpenAI-compatible endpoints go through the platform’s adapter).
          </p>
          <p className="text-sm text-white/90 font-medium mb-2">
            <strong>Sequential (Agent 1 → Agent 2):</strong> One agent’s output is the next agent’s input. Use agents without the A2A badge. Best for pipelines and step-by-step tasks.
          </p>
          <p className="text-sm text-white/90 font-medium">
            <strong>A2A (async, peer collaboration):</strong> Agents work asynchronously and communicate as peers. Use agents with the &quot;A2A&quot; badge when your requirements need peer-to-peer collaboration instead of a simple handoff.
          </p>
          {completionWithHint && completionWithHint.workflow_collaboration_reason && (
            <div className="mt-4 pt-4 border-t border-primary-500/30">
              <p className="text-sm font-semibold text-white">
                Based on your requirements: {completionWithHint.workflow_collaboration_reason}
              </p>
              <p className="text-sm text-primary-300 mt-1">
                {completionWithHint.workflow_collaboration_hint === 'async_a2a'
                  ? 'Consider selecting A2A-enabled agents when you build the workflow.'
                  : 'Standard (sequential) agents are suitable for this workflow.'}
              </p>
            </div>
          )}
        </div>
      )}

      {conversation.length === 0 && !hasAnalysis && (
        <div className="text-center py-12">
          {files && files.length > 0 ? (
            <p className="text-white/60 text-lg font-medium">
              Use <strong className="text-white/80">Analyze Documents</strong> on the Job screen (above) to have the AI review your BRD. After building a workflow, click <strong className="text-white/80">Generate clarification questions</strong> above to get questions based on the workflow and BRD. Answer any questions here, then go to Preview Cost → Execute.
            </p>
          ) : (
            <p className="text-white/60 text-lg font-medium">Upload documents on the Job screen, then follow the order above.</p>
          )}
        </div>
      )}

      {conversation.length > 0 && (
        <div className="space-y-5 mb-8">
          {conversation.map((item, index) => (
            <div key={index} className="border-2 border-dark-200/50 rounded-2xl p-6 bg-dark-200/30 backdrop-blur-sm">
              {item.type === 'analysis' && (
                <div className="bg-blue-500/20 border-l-4 border-blue-500/50 p-5 rounded-xl">
                  <h3 className="font-black text-blue-400 mb-3 text-lg">📄 Document Analysis</h3>
                  <p className="text-sm text-white/80 whitespace-pre-wrap font-medium leading-relaxed">{item.content}</p>
                </div>
              )}
              
              {item.type === 'question' && (
                <div className={`p-5 rounded-xl border-2 ${
                  item.answer ? 'bg-green-500/10 border-green-500/30' : 'bg-yellow-500/10 border-yellow-500/30'
                }`}>
                  <div className="flex items-start gap-4">
                    <div className="flex-shrink-0 w-10 h-10 bg-gradient-to-br from-blue-500 to-blue-700 text-white rounded-full flex items-center justify-center font-black text-lg shadow-lg">
                      Q
                    </div>
                    <div className="flex-1">
                      <p className="font-bold text-white mb-3 text-lg">{item.question}</p>
                      {item.answer && (
                        <div className="mt-4 p-4 bg-dark-50/50 rounded-xl border border-dark-200/50">
                          <div className="flex items-start gap-4">
                            <div className="flex-shrink-0 w-10 h-10 bg-gradient-to-br from-green-500 to-green-700 text-white rounded-full flex items-center justify-center font-black text-lg shadow-lg">
                              A
                            </div>
                            <p className="text-sm text-white/90 font-medium leading-relaxed">{item.answer}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {item.type === 'completion' && (
                <div className="bg-green-500/20 border-l-4 border-green-500/50 p-5 rounded-xl">
                  <h3 className="font-black text-green-400 mb-3 text-lg">✅ Problem Understood - Solutions Ready</h3>
                  <p className="text-sm text-white/80 font-medium mb-4">{item.message || item.content}</p>
                  {item.solutions && item.solutions.length > 0 && (
                    <div className="mt-5">
                      <h4 className="font-bold text-blue-400 mb-3 text-base">💡 Proposed Solutions:</h4>
                      <ul className="list-disc list-inside space-y-2">
                        {item.solutions.map((sol, idx) => (
                          <li key={idx} className="text-sm text-white/80 font-medium">{sol}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {item.recommendations && item.recommendations.length > 0 && (
                    <div className="mt-5">
                      <h4 className="font-bold text-green-400 mb-3 text-base">📋 Recommendations:</h4>
                      <ul className="list-disc list-inside space-y-2">
                        {item.recommendations.map((rec, idx) => (
                          <li key={idx} className="text-sm text-white/80 font-medium">{rec}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {item.next_steps && item.next_steps.length > 0 && (
                    <div className="mt-5">
                      <h4 className="font-bold text-primary-400 mb-3 text-base">🚀 Next Steps:</h4>
                      <ul className="list-disc list-inside space-y-2">
                        {item.next_steps.map((step, idx) => (
                          <li key={idx} className="text-sm text-white/80 font-medium">{step}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {unansweredQuestion && !hasCompletion && (
        <div className="border-t border-dark-200/50 pt-6">
          <div className="mb-5">
            <label className="block text-base font-bold text-white mb-3">
              Answer the question:
            </label>
            <p className="text-white/90 mb-4 p-4 bg-yellow-500/10 rounded-xl border-2 border-yellow-500/30 font-medium">
              {unansweredQuestion.question}
            </p>
          </div>
          <div className="flex gap-4">
            <textarea
              value={currentAnswer}
              onChange={(e) => setCurrentAnswer(e.target.value)}
              placeholder="Type your answer here..."
              rows={5}
              className="flex-1 px-5 py-4 bg-white border-2 border-gray-300 rounded-xl text-gray-900 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500 text-lg font-medium resize-none"
            />
            <button
              onClick={handleSubmitAnswer}
              disabled={isSubmitting || !currentAnswer.trim()}
              className="px-8 py-4 bg-gradient-to-r from-primary-500 to-primary-700 text-white rounded-xl font-bold hover:shadow-2xl hover:shadow-primary-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 self-end"
            >
              {isSubmitting ? (
                <span className="flex items-center gap-2">
                  <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                  Submitting...
                </span>
              ) : (
                'Submit Answer'
              )}
            </button>
          </div>
        </div>
      )}

      {hasQuestions && !unansweredQuestion && !hasCompletion && (
        <div className="bg-green-500/20 border-2 border-green-500/30 rounded-xl p-6 text-center">
          <p className="text-green-400 font-black text-lg">✅ All questions answered!</p>
          <p className="text-sm text-white/70 mt-2 font-medium">You can now proceed with workflow setup.</p>
        </div>
      )}
    </div>
  )
}
