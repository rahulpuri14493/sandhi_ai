from sqlalchemy.orm import Session
from models.job import Job, WorkflowStep
from models.agent import Agent
from models.transaction import Transaction, TransactionStatus, Earnings, EarningsStatus
from models.communication import AgentCommunication
from schemas.job import WorkflowPreview, WorkflowStepResponse
from core.config import settings
import json


class PaymentProcessor:
    def __init__(self, db: Session):
        self.db = db
        self.commission_rate = settings.PLATFORM_COMMISSION_RATE
    
    def calculate_job_cost(self, job_id: int) -> WorkflowPreview:
        """Calculate total cost for a job including tasks, communications, and commission"""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        
        workflow_steps = self.db.query(WorkflowStep).filter(
            WorkflowStep.job_id == job_id
        ).order_by(WorkflowStep.step_order).all()
        
        if not workflow_steps:
            return WorkflowPreview(
                steps=[],
                total_cost=0.0,
                breakdown={
                    "task_costs": 0.0,
                    "communication_costs": 0.0,
                    "commission": 0.0
                }
            )
        
        # Calculate task costs
        task_costs = 0.0
        step_responses = []
        for step in workflow_steps:
            agent = self.db.query(Agent).filter(Agent.id == step.agent_id).first()
            if agent:
                task_costs += agent.price_per_task
            # Use agent.price_per_task for preview (step.cost is only set after execution)
            step_cost = (agent.price_per_task if agent else 0.0) if (step.cost is None or step.cost == 0) else step.cost
            
            step_platform = step_conn = None
            if getattr(step, "allowed_platform_tool_ids", None):
                try:
                    step_platform = json.loads(step.allowed_platform_tool_ids)
                except (json.JSONDecodeError, TypeError):
                    pass
            if getattr(step, "allowed_connection_ids", None):
                try:
                    step_conn = json.loads(step.allowed_connection_ids)
                except (json.JSONDecodeError, TypeError):
                    pass
            step_responses.append(WorkflowStepResponse(
                id=step.id,
                job_id=step.job_id,
                agent_id=step.agent_id,
                agent_name=agent.name if agent else None,
                step_order=step.step_order,
                input_data=step.input_data,
                output_data=step.output_data,
                status=step.status,
                cost=step_cost,
                started_at=step.started_at,
                completed_at=step.completed_at,
                depends_on_previous=getattr(step, "depends_on_previous", True),
                allowed_platform_tool_ids=step_platform,
                allowed_connection_ids=step_conn,
                tool_visibility=getattr(step, "tool_visibility", None),
            ))
        
        # Estimate communication costs (between consecutive steps)
        communication_costs = 0.0
        for i in range(len(workflow_steps) - 1):
            from_step = workflow_steps[i]
            to_step = workflow_steps[i + 1]
            
            from_agent = self.db.query(Agent).filter(Agent.id == from_step.agent_id).first()
            to_agent = self.db.query(Agent).filter(Agent.id == to_step.agent_id).first()
            
            if from_agent and to_agent:
                # Both agents get paid for communication
                communication_costs += from_agent.price_per_communication
                communication_costs += to_agent.price_per_communication
        
        # Calculate commission
        subtotal = task_costs + communication_costs
        commission = subtotal * self.commission_rate
        
        # Total cost
        total_cost = subtotal + commission
        
        # Update job total cost
        job.total_cost = total_cost
        self.db.commit()
        
        return WorkflowPreview(
            steps=step_responses,
            total_cost=total_cost,
            breakdown={
                "task_costs": task_costs,
                "communication_costs": communication_costs,
                "commission": commission
            }
        )
    
    def process_payment(self, job_id: int) -> Transaction:
        """Process payment for a job (mock implementation)"""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        
        # Check if transaction already exists
        existing_transaction = self.db.query(Transaction).filter(
            Transaction.job_id == job_id
        ).first()
        
        if existing_transaction:
            return existing_transaction
        
        # Create transaction
        transaction = Transaction(
            job_id=job_id,
            payer_id=job.business_id,
            total_amount=job.total_cost,
            platform_commission=job.total_cost * self.commission_rate,
            status=TransactionStatus.COMPLETED  # Mock: always succeeds
        )
        self.db.add(transaction)
        self.db.commit()
        self.db.refresh(transaction)
        
        return transaction
    
    def distribute_earnings(self, job_id: int):
        """Distribute earnings to developers after job completion"""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        
        transaction = self.db.query(Transaction).filter(
            Transaction.job_id == job_id
        ).first()
        
        if not transaction:
            raise ValueError("Transaction not found. Payment must be processed first.")
        
        # Get all workflow steps
        workflow_steps = self.db.query(WorkflowStep).filter(
            WorkflowStep.job_id == job_id
        ).all()
        
        # Distribute earnings for tasks
        for step in workflow_steps:
            agent = self.db.query(Agent).filter(Agent.id == step.agent_id).first()
            if agent:
                earning = Earnings(
                    developer_id=agent.developer_id,
                    transaction_id=transaction.id,
                    workflow_step_id=step.id,
                    amount=agent.price_per_task,
                    status=EarningsStatus.PAID
                )
                self.db.add(earning)
        
        # Distribute earnings for communications
        # Need to specify join condition explicitly since AgentCommunication has two FKs to WorkflowStep
        # Get communications where either from_step or to_step belongs to this job
        from sqlalchemy import or_
        
        # Get workflow step IDs for this job
        workflow_step_ids = [step.id for step in workflow_steps]
        
        if workflow_step_ids:
            # Query communications where either from_step or to_step is in this job's workflow steps
            communications = self.db.query(AgentCommunication).filter(
                or_(
                    AgentCommunication.from_workflow_step_id.in_(workflow_step_ids),
                    AgentCommunication.to_workflow_step_id.in_(workflow_step_ids)
                )
            ).all()
        else:
            communications = []
        
        for comm in communications:
            from_agent = self.db.query(Agent).filter(Agent.id == comm.from_agent_id).first()
            to_agent = self.db.query(Agent).filter(Agent.id == comm.to_agent_id).first()
            
            if from_agent:
                earning = Earnings(
                    developer_id=from_agent.developer_id,
                    transaction_id=transaction.id,
                    communication_id=comm.id,
                    amount=from_agent.price_per_communication,
                    status=EarningsStatus.PAID
                )
                self.db.add(earning)
            
            if to_agent:
                earning = Earnings(
                    developer_id=to_agent.developer_id,
                    transaction_id=transaction.id,
                    communication_id=comm.id,
                    amount=to_agent.price_per_communication,
                    status=EarningsStatus.PAID
                )
                self.db.add(earning)
        
        self.db.commit()
