export type UserRole = "business" | "developer";

export interface User {
  id: number;
  email: string;
  role: UserRole;
  created_at: string;
}

export interface Agent {
  id: number;
  developer_id: number;
  name: string;
  description?: string;
  capabilities?: string[];
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  price_per_task: number;
  price_per_communication: number;
  api_endpoint?: string;
  plugin_config?: Record<string, any>;
  status: "active" | "inactive" | "pending";
  created_at: string;
}

export interface Job {
  id: number;
  business_id: number;
  title: string;
  description?: string;
  status: "draft" | "pending_approval" | "approved" | "in_progress" | "completed" | "failed" | "cancelled";
  total_cost: number;
  created_at: string;
  completed_at?: string;
  workflow_steps?: WorkflowStep[];
}

export interface WorkflowStep {
  id: number;
  job_id: number;
  agent_id: number;
  step_order: number;
  input_data?: string;
  output_data?: string;
  status: string;
  cost: number;
  started_at?: string;
  completed_at?: string;
}

export interface WorkflowPreview {
  steps: WorkflowStep[];
  total_cost: number;
  breakdown: {
    task_costs: number;
    communication_costs: number;
    commission: number;
  };
}

export interface Transaction {
  id: number;
  job_id: number;
  payer_id: number;
  total_amount: number;
  platform_commission: number;
  status: "pending" | "completed" | "failed" | "refunded";
  created_at: string;
}

export interface Earnings {
  id: number;
  developer_id: number;
  transaction_id: number;
  workflow_step_id?: number;
  communication_id?: number;
  amount: number;
  status: "pending" | "paid" | "failed";
  created_at: string;
}
