from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
from models.job import JobStatus


class JobCreate(BaseModel):
    title: str
    description: Optional[str] = None
    agent_ids: Optional[List[int]] = []  # For auto-split
    workflow_steps: Optional[List["WorkflowStepCreate"]] = []  # For manual assignment


class JobUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[JobStatus] = None


class WorkflowStepCreate(BaseModel):
    agent_id: int
    step_order: int
    input_data: Optional[Dict[str, Any]] = None


class JobResponse(BaseModel):
    id: int
    business_id: int
    title: str
    description: Optional[str]
    status: JobStatus
    total_cost: float
    created_at: datetime
    completed_at: Optional[datetime]
    workflow_steps: Optional[List["WorkflowStepResponse"]] = []
    files: Optional[List[Dict[str, Any]]] = None  # File metadata
    failure_reason: Optional[str] = None  # Reason for job failure

    class Config:
        from_attributes = True
    
    @classmethod
    def model_validate(cls, obj, **kwargs):
        # Parse files JSON string if it exists
        if hasattr(obj, 'files') and obj.files:
            try:
                files_data = json.loads(obj.files)
                obj_dict = {k: v for k, v in obj.__dict__.items()}
                obj_dict['files'] = files_data
                # Remove file paths from response for security, only return metadata
                for file_info in files_data:
                    file_info.pop('path', None)
                obj = type(obj)(**obj_dict)
            except (json.JSONDecodeError, TypeError):
                pass
        return super().model_validate(obj, **kwargs)


class WorkflowStepResponse(BaseModel):
    id: int
    job_id: int
    agent_id: int
    agent_name: Optional[str] = None
    step_order: int
    input_data: Optional[str]
    output_data: Optional[str]
    status: str
    cost: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class WorkflowPreview(BaseModel):
    steps: List[WorkflowStepResponse]
    total_cost: float
    breakdown: Dict[str, float]  # task_costs, communication_costs, commission


# Update forward references
JobResponse.model_rebuild()
WorkflowStepResponse.model_rebuild()
