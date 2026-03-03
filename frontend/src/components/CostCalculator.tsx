import type { WorkflowPreview } from '../lib/types'

interface CostCalculatorProps {
  preview: WorkflowPreview
}

export function CostCalculator({ preview }: CostCalculatorProps) {
  return (
    <div>
      <h2 className="text-3xl font-black text-white tracking-tight mb-6">Cost Breakdown</h2>
      <div className="space-y-4">
        <div className="flex justify-between items-center p-4 bg-dark-200/30 rounded-xl border border-dark-300">
          <span className="text-white/70 font-medium">Task Costs:</span>
          <span className="font-black text-white text-xl">${preview.breakdown.task_costs.toFixed(2)}</span>
        </div>
        <div className="flex justify-between items-center p-4 bg-dark-200/30 rounded-xl border border-dark-300">
          <span className="text-white/70 font-medium">Communication Costs:</span>
          <span className="font-black text-white text-xl">${preview.breakdown.communication_costs.toFixed(2)}</span>
        </div>
        <div className="flex justify-between items-center p-4 bg-dark-200/30 rounded-xl border border-dark-300">
          <span className="text-white/70 font-medium">Platform Commission:</span>
          <span className="font-black text-white text-xl">${preview.breakdown.commission.toFixed(2)}</span>
        </div>
        <div className="border-t-2 border-dark-200/50 pt-4 flex justify-between items-center">
          <span className="text-2xl font-black text-white">Total Cost:</span>
          <span className="text-3xl font-black text-primary-400">${preview.total_cost.toFixed(2)}</span>
        </div>
      </div>

      {preview.steps.length > 0 && (
        <div className="mt-8">
          <h3 className="font-black text-white mb-5 text-xl">Workflow Steps</h3>
          <div className="space-y-3">
            {preview.steps.map((step) => (
              <div key={step.id} className="flex items-center justify-between p-4 bg-dark-200/30 rounded-xl border border-dark-300">
                <span className="text-white/80 font-medium">Step {step.step_order}: Agent {step.agent_id}</span>
                <span className="text-sm text-primary-400 font-bold">${step.cost.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
