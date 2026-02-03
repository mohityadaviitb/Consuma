"""
Pydantic models for request/response validation.
"""
from pydantic import BaseModel, Field, field_validator, HttpUrl
from typing import Optional, Any, Literal
from datetime import datetime
from enum import Enum
import uuid


class RequestMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"


class RequestStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CALLBACK_PENDING = "callback_pending"
    CALLBACK_SUCCESS = "callback_success"
    CALLBACK_FAILED = "callback_failed"


# --- Input Models ---

class WorkPayload(BaseModel):
    """
    The 'work' payload - shared between sync and async.
    For this demo, we compute a hash of the data with optional iterations.
    """
    data: str = Field(..., min_length=1, max_length=10000, description="Input data to process")
    iterations: int = Field(default=1000, ge=1, le=1000000, description="Work iterations (affects compute time)")
    
    @field_validator('data')
    @classmethod
    def data_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('data cannot be empty or whitespace only')
        return v


class AsyncWorkPayload(WorkPayload):
    """Async payload includes callback_url."""
    callback_url: str = Field(..., description="URL to POST result to")
    
    @field_validator('callback_url')
    @classmethod
    def validate_callback_url(cls, v: str) -> str:
        # Basic URL validation - more thorough SSRF check happens at runtime
        if not v.startswith(('http://', 'https://')):
            raise ValueError('callback_url must be http or https')
        if len(v) > 2048:
            raise ValueError('callback_url too long')
        return v


# --- Output Models ---

class WorkResult(BaseModel):
    """Result of the work computation."""
    request_id: str
    input_hash: str
    output_hash: str
    iterations: int
    processing_time_ms: float


class SyncResponse(BaseModel):
    """Response for POST /sync."""
    status: Literal["success"] = "success"
    result: WorkResult


class AsyncAckResponse(BaseModel):
    """Acknowledgment response for POST /async."""
    status: Literal["accepted"] = "accepted"
    request_id: str
    message: str = "Request queued for processing"


class CallbackPayload(BaseModel):
    """Payload sent to callback_url."""
    request_id: str
    status: Literal["success", "error"]
    result: Optional[WorkResult] = None
    error: Optional[str] = None
    timestamp: datetime


class RequestRecord(BaseModel):
    """Stored request record."""
    id: str
    mode: RequestMode
    status: RequestStatus
    payload_hash: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    callback_url: Optional[str] = None
    callback_attempts: int = 0
    callback_last_error: Optional[str] = None
    result: Optional[WorkResult] = None


class RequestListResponse(BaseModel):
    """Response for GET /requests."""
    total: int
    requests: list[RequestRecord]


class HealthResponse(BaseModel):
    """Response for GET /healthz."""
    status: Literal["healthy", "degraded", "unhealthy"]
    version: str = "1.0.0"
    queue_size: int
    queue_capacity: int
    active_workers: int
    database_ok: bool


class ErrorResponse(BaseModel):
    """Standard error response."""
    status: Literal["error"] = "error"
    error: str
    request_id: Optional[str] = None
