/**
 * Extract the display text from a workflow step's output_data.
 * Returns only the agent's answer (content), not the full JSON.
 */
export function getStepOutputDisplayText(outputData: unknown): string {
  if (outputData == null) return ''
  if (typeof outputData === 'object' && outputData !== null && 'error' in outputData) {
    const err = (outputData as { error?: string }).error
    return typeof err === 'string' ? err : String(err ?? 'Unknown error')
  }
  if (typeof outputData === 'object' && outputData !== null) {
    const o = outputData as Record<string, unknown>
    // OpenAI shape
    const choices = o.choices as Array<{ message?: { content?: string } }> | undefined
    if (Array.isArray(choices) && choices[0]?.message?.content != null) {
      return String(choices[0].message.content)
    }
    // A2A shape: content
    if (o.content != null && o.content !== '') {
      return String(o.content)
    }
    // A2A shape: raw_message.parts[0].text
    const rawMessage = o.raw_message as { parts?: Array<{ text?: string }> } | undefined
    const parts = rawMessage?.parts
    if (Array.isArray(parts) && parts[0]?.text != null) {
      return String(parts[0].text)
    }
  }
  if (typeof outputData === 'string') return outputData
  return String(outputData)
}
