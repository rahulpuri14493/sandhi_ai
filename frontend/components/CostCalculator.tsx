import type { WorkflowPreview } from '@/lib/types'

interface CostCalculatorProps {
  preview: WorkflowPreview
}

export function CostCalculator({ preview }: CostCalculatorProps) {
  return (
    <div>
      <h2 className="text-2xl font-bold mb-4">Cost Breakdown</h2>
      <div className="space-y-3">
        <div className="flex justify-between">
          <span>Task Costs:</span>
          <span className="font-semibold">${preview.breakdown.task_costs.toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span>Communication Costs:</span>
          <span className="font-semibold">${preview.breakdown.communication_costs.toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span>Platform Commission:</span>
          <span className="font-semibold">${preview.breakdown.commission.toFixed(2)}</span>
        </div>
        <div className="border-t pt-3 flex justify-between text-xl font-bold">
          <span>Total Cost:</span>
          <span className="text-primary-600">${preview.total_cost.toFixed(2)}</span>
        </div>
      </div>

      {preview.steps.length > 0 && (
        <div className="mt-6">
          <h3 className="font-semibold mb-3">Workflow Steps</h3>
          <div className="space-y-2">
            {preview.steps.map((step, idx) => (
              <div key={step.id} className="flex items-center justify-between p-3 bg-gray-50 rounded">
                <span>Step {step.step_order}: Agent {step.agent_id}</span>
                <span className="text-sm text-gray-600">${step.cost.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
