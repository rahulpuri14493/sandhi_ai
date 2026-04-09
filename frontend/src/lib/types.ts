export type UserRole = "business" | "developer";

export interface User {
  id: number;
  email: string;
  role: UserRole;
  created_at: string;
}

export type PricingModel = "pay_per_use" | "monthly" | "quarterly";

export interface Agent {
  id: number;
  developer_id: number;
  name: string;
  description?: string;
  capabilities?: string[];
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  pricing_model: PricingModel;
  price_per_task: number;
  price_per_communication: number;
  monthly_price?: number;
  quarterly_price?: number;
  api_endpoint?: string;
  api_key?: string;
  llm_model?: string;
  temperature?: number;
  plugin_config?: Record<string, any>;
  /** Use A2A (Agent-to-Agent) protocol for invocation when true */
  a2a_enabled?: boolean;
  status: "active" | "inactive" | "pending";
  created_at: string;
  /** Overall average rating (1–5); only in list response for marketplace */
  average_rating?: number | null;
  /** Number of reviews; only in list response for marketplace */
  review_count?: number | null;
}

export interface AgentReviewSummary {
  agent_id: number;
  average_rating: number;
  total_count: number;
  rating_distribution: Record<number, number>;
}

export interface AgentReview {
  id: number;
  agent_id: number;
  user_id: number;
  rating: number;
  review_text: string | null;
  created_at: string;
  updated_at?: string | null;
  is_own: boolean;
}

export interface AgentReviewList {
  items: AgentReview[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobFile {
  id: string;
  name: string;
  type: string;
  size: number;
}

/** Hint from BRD analysis: sequential = Agent1 output → Agent2 input; async_a2a = agents collaborate as peers */
export type WorkflowCollaborationHint = "sequential" | "async_a2a";

export interface ConversationItem {
  type: "question" | "answer" | "analysis" | "completion";
  question?: string;
  answer?: string;
  content?: string;
  message?: string;
  recommendations?: string[];
  solutions?: string[];
  next_steps?: string[];
  /** From BRD: suggest "sequential" (pipeline) or "async_a2a" (peer collaboration) */
  workflow_collaboration_hint?: WorkflowCollaborationHint | null;
  workflow_collaboration_reason?: string | null;
  timestamp?: string;
  /** Workflow Q&A: which step's assigned agent asked this */
  workflow_step_id?: number;
  agent_id?: number;
  agent_name?: string | null;
}

export interface Job {
  id: number;
  business_id: number;
  title: string;
  description?: string;
  status: "draft" | "pending_approval" | "approved" | "in_queue" | "in_progress" | "completed" | "failed" | "cancelled";
  total_cost: number;
  created_at: string;
  completed_at?: string;
  workflow_steps?: WorkflowStep[];
  files?: JobFile[];
  conversation?: ConversationItem[];
  failure_reason?: string;
  /** Platform tool IDs in scope for this job (empty/undefined = all business tools). */
  allowed_platform_tool_ids?: number[] | null;
  /** MCP connection IDs in scope for this job (empty/undefined = all). */
  allowed_connection_ids?: number[] | null;
  /** Restrict what tool info agents see: full | names_only | none. Credentials never shared. */
  tool_visibility?: 'full' | 'names_only' | 'none' | null;
  /** Who triggers MCP writes from persisted output artifact references. */
  write_execution_mode?: 'platform' | 'agent' | 'ui_only' | null;
  /** Persisted AI output artifact format in object storage. */
  output_artifact_format?: 'jsonl' | 'json' | null;
  /** Universal output contract for downstream MCP write execution. */
  output_contract?: Record<string, any> | null;
  /** True when in-progress job exceeds stuck threshold — frontend shows cancel button. */
  show_cancel_option?: boolean;
  /** From job's schedule, used for countdown timer on in_queue jobs. */
  scheduled_at?: string | null;
}

export interface WorkflowStep {
  id: number;
  job_id: number;
  agent_id: number;
  agent_name?: string;
  step_order: number;
  input_data?: string;
  output_data?: string;
  status: string;
  cost: number;
  started_at?: string;
  completed_at?: string;
  depends_on_previous?: boolean;
  /** Tools this step (agent) can use; empty/undefined = use job-level tools. */
  allowed_platform_tool_ids?: number[] | null;
  allowed_connection_ids?: number[] | null;
  /** Override job tool_visibility for this step: full | names_only | none. */
  tool_visibility?: 'full' | 'names_only' | 'none' | null;
  live_phase?: string | null;
  live_phase_started_at?: string | null;
  live_reason_code?: string | null;
  live_reason_detail?: string | null;
  live_trace_id?: string | null;
  live_attempt?: number | null;
  last_progress_at?: string | null;
  last_activity_at?: string | null;
  stuck_since?: string | null;
  stuck_reason?: string | null;
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

export type HiringStatus = "open" | "closed" | "filled";
export type NominationStatus = "pending" | "approved" | "rejected";

export interface HiringPosition {
  id: number;
  business_id: number;
  title: string;
  description?: string;
  requirements?: string;
  status: HiringStatus;
  created_at: string;
  updated_at: string;
  nomination_count?: number;
}

export interface AgentNomination {
  id: number;
  hiring_position_id: number;
  agent_id: number;
  developer_id: number;
  cover_letter?: string;
  status: NominationStatus;
  reviewed_by?: number;
  reviewed_at?: string;
  review_notes?: string;
  created_at: string;
  agent_name?: string;
  developer_email?: string;
  hiring_position_title?: string;
}

export interface HiringPositionWithNominations extends HiringPosition {
  nominations: AgentNomination[];
}

export type ScheduleStatus = 'active' | 'inactive'

export interface JobSchedule {
  id: number
  job_id: number
  status: ScheduleStatus
  timezone: string
  scheduled_at: string
  last_run_time: string | null
  next_run_time: string | null
  created_at: string
}

export interface JobScheduleWithJob extends JobSchedule {
  job_title: string
  job_status: string
}

/** GET /jobs/:id/planner-pipeline — latest BRD, task split, and tool suggestion payloads */
export interface PlannerPipelineBundle {
  schema_version: string
  job_id: number
  brd_analysis: Record<string, unknown> | null
  task_split: Record<string, unknown> | null
  tool_suggestion: Record<string, unknown> | null
  artifact_ids: {
    brd_analysis?: number | null
    task_split?: number | null
    tool_suggestion?: number | null
  }
}
