import { useState, useEffect } from 'react'
import { jobsAPI } from '../lib/api'
import type { ConversationItem } from '../lib/types'

interface DocumentConversationProps {
  jobId: number
  files?: Array<{ id: string; name: string }>
  initialConversation?: ConversationItem[]
  onConversationUpdate?: (conversation: ConversationItem[]) => void
}

export function DocumentConversation({ jobId, files, initialConversation = [], onConversationUpdate }: DocumentConversationProps) {
  const [conversation, setConversation] = useState<ConversationItem[]>(initialConversation)
  const [currentAnswer, setCurrentAnswer] = useState('')
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setConversation(initialConversation)
  }, [initialConversation])

  const handleAnalyze = async () => {
    if (!files || files.length === 0) {
      setError('No documents uploaded')
      return
    }

    setIsAnalyzing(true)
    setError('')
    try {
      const result = await jobsAPI.analyzeDocuments(jobId)
      setConversation(result.conversation || [])
      if (onConversationUpdate) {
        onConversationUpdate(result.conversation || [])
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to analyze documents')
    } finally {
      setIsAnalyzing(false)
    }
  }

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
      const hasUnanswered = updatedConversation.some(item => 
        item.type === 'question' && (!item.answer || item.answer.trim() === '')
      )
      const hasCompletion = updatedConversation.some(item => item.type === 'completion')
      
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

  const getUnansweredQuestion = (): ConversationItem | null => {
    // Find questions that don't have an answer (checking for null, undefined, or empty string)
    return conversation.find(item => 
      item.type === 'question' && 
      (!item.answer || item.answer.trim() === '')
    ) || null
  }

  const unansweredQuestion = getUnansweredQuestion()
  const hasQuestions = conversation.some(item => item.type === 'question')
  const hasAnalysis = conversation.some(item => item.type === 'analysis')
  const hasCompletion = conversation.some(item => item.type === 'completion')

  return (
    <div className="bg-dark-100/50 backdrop-blur-xl rounded-2xl shadow-2xl p-8 border border-dark-200/50">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-4xl font-black text-white tracking-tight">Document Analysis & Q&A</h2>
        {!hasAnalysis && files && files.length > 0 && (
          <button
            onClick={handleAnalyze}
            disabled={isAnalyzing}
            className="px-6 py-3 bg-gradient-to-r from-blue-500 to-blue-700 text-white rounded-xl font-bold hover:shadow-2xl hover:shadow-blue-500/50 hover:scale-105 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100"
          >
            {isAnalyzing ? (
              <span className="flex items-center gap-2">
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
                Analyzing...
              </span>
            ) : (
              'Analyze Documents'
            )}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/20 border-2 border-red-500/50 text-red-400 rounded-xl font-semibold">
          {error}
        </div>
      )}

      {conversation.length === 0 && !hasAnalysis && (
        <div className="text-center py-12">
          {files && files.length > 0 ? (
            <p className="text-white/60 text-lg font-medium">Click "Analyze Documents" to have AI Assistant review your documents and ask clarifying questions.</p>
          ) : (
            <p className="text-white/60 text-lg font-medium">Upload documents to enable document analysis and Q&A.</p>
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
