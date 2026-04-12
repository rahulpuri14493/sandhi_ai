import type { JobRerunResponse } from './types'

export function formatRerunStartedMessage(resp: JobRerunResponse): string {
  const mode = (resp.mode || 'resume').toString().toUpperCase()
  const reused = typeof resp.steps_reused_count === 'number' ? resp.steps_reused_count : 0
  const rerun = typeof resp.steps_rerun_count === 'number' ? resp.steps_rerun_count : 0
  const start = typeof resp.resume_start_step_order === 'number' ? resp.resume_start_step_order : 1
  return `Rerun started (${mode}). Reused steps: ${reused}. Rerun steps: ${rerun}. Start step: ${start}.`
}
