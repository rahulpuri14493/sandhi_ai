import { describe, it, expect } from 'vitest'
import { getStepOutputDisplayText } from '../src/lib/formatStepOutput'

describe('getStepOutputDisplayText', () => {
  it('returns empty string for null/undefined', () => {
    expect(getStepOutputDisplayText(null)).toBe('')
    expect(getStepOutputDisplayText(undefined)).toBe('')
  })

  it('extracts error field when present', () => {
    expect(getStepOutputDisplayText({ error: 'bad' })).toBe('bad')
    expect(getStepOutputDisplayText({ error: 123 })).toBe('123')
    expect(getStepOutputDisplayText({ error: null })).toBe('Unknown error')
  })

  it('extracts OpenAI choices[0].message.content', () => {
    expect(
      getStepOutputDisplayText({ choices: [{ message: { content: 'hello' } }] })
    ).toBe('hello')
  })

  it('extracts A2A content', () => {
    expect(getStepOutputDisplayText({ content: 'hi' })).toBe('hi')
  })

  it('extracts raw_message.parts[0].text', () => {
    expect(
      getStepOutputDisplayText({ raw_message: { parts: [{ text: 'part' }] } })
    ).toBe('part')
  })

  it('extracts agent_output from platform step envelope', () => {
    expect(
      getStepOutputDisplayText({
        agent_output: { records: [{ customer_id: 'C-1', decision: 'nfa' }] },
        artifact_ref: { format: 'jsonl' },
      })
    ).toContain('"customer_id": "C-1"')
  })

  it('formats object content as JSON instead of [object Object]', () => {
    expect(
      getStepOutputDisplayText({ content: { score: 0.91, label: 'ok' } })
    ).toContain('"score": 0.91')
  })

  it('falls back to stringification', () => {
    expect(getStepOutputDisplayText('s')).toBe('s')
    expect(getStepOutputDisplayText(7)).toBe('7')
  })
})

