"""
Main FastAPI application with all endpoints.
"""
import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app import database
from app.callback_worker import worker_pool
from app.config import settings
from app.models import (
    AsyncAckResponse,
    AsyncWorkPayload,
    ErrorResponse,
    HealthResponse,
    RequestListResponse,
    RequestMode,
    RequestRecord,
    RequestStatus,
    SyncResponse,
    WorkPayload,
)
from app.work import compute_work_async

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup
    logger.info("Starting up...")
    await database.init_database()
    await worker_pool.start()
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    await worker_pool.stop()


app = FastAPI(
    title="Sync vs Async API",
    description="Backend demonstrating sync and async (callback) request patterns",
    version="1.0.0",
    lifespan=lifespan
)


# --- Exception Handlers ---

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.detail).model_dump()
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="Internal server error").model_dump()
    )


# --- Endpoints ---

@app.post("/sync", response_model=SyncResponse)
async def sync_endpoint(payload: WorkPayload):
    """
    Synchronous work endpoint.
    
    Performs work and returns result inline.
    Suitable for quick operations or when caller can wait.
    """
    request_id = str(uuid.uuid4())
    
    # Create request record
    record = RequestRecord(
        id=request_id,
        mode=RequestMode.SYNC,
        status=RequestStatus.PROCESSING,
        payload_hash=hashlib.sha256(payload.data.encode()).hexdigest()[:16],
        created_at=datetime.utcnow()
    )
    await database.save_request(record)
    
    try:
        # Perform the work
        result = await compute_work_async(request_id, payload)
        
        # Update record with result
        record.status = RequestStatus.COMPLETED
        record.completed_at = datetime.utcnow()
        record.result = result
        await database.save_request(record)
        
        return SyncResponse(result=result)
        
    except Exception as e:
        logger.exception(f"Sync request {request_id} failed: {e}")
        record.status = RequestStatus.CALLBACK_FAILED  # Reusing status for error
        record.callback_last_error = str(e)
        await database.save_request(record)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/async", response_model=AsyncAckResponse)
async def async_endpoint(payload: AsyncWorkPayload):
    """
    Asynchronous work endpoint.
    
    Accepts request, returns acknowledgment immediately,
    processes work in background, and POSTs result to callback_url.
    
    Suitable for long-running operations or when caller shouldn't block.
    """
    request_id = str(uuid.uuid4())
    
    # Create request record
    record = RequestRecord(
        id=request_id,
        mode=RequestMode.ASYNC,
        status=RequestStatus.PENDING,
        payload_hash=hashlib.sha256(payload.data.encode()).hexdigest()[:16],
        created_at=datetime.utcnow(),
        callback_url=payload.callback_url
    )
    await database.save_request(record)
    
    # Enqueue for background processing
    success = await worker_pool.enqueue(request_id, payload)
    
    if not success:
        # Queue is full - reject with 503
        record.status = RequestStatus.CALLBACK_FAILED
        record.callback_last_error = "Server overloaded - queue full"
        await database.save_request(record)
        raise HTTPException(
            status_code=503,
            detail="Server overloaded, please retry later"
        )
    
    return AsyncAckResponse(
        request_id=request_id,
        message="Request accepted and queued for processing"
    )


@app.get("/requests", response_model=RequestListResponse)
async def list_requests(
    mode: Optional[RequestMode] = Query(None, description="Filter by mode (sync/async)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """
    List recent requests with optional filtering.
    
    Useful for:
    - Debugging and monitoring
    - Tracing async request lifecycle
    - Auditing
    """
    total, requests = await database.list_requests(mode, limit, offset)
    return RequestListResponse(total=total, requests=requests)


@app.get("/requests/{request_id}", response_model=RequestRecord)
async def get_request(request_id: str):
    """
    Get details of a specific request.
    
    Includes:
    - Current status
    - Result (if completed)
    - Callback delivery status and errors
    """
    record = await database.get_request(request_id)
    if not record:
        raise HTTPException(status_code=404, detail="Request not found")
    return record


@app.get("/healthz", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    
    Reports:
    - Overall health status
    - Queue size and capacity
    - Active workers
    - Database connectivity
    """
    stats = worker_pool.get_stats()
    db_ok = await database.check_database_health()
    
    # Determine health status
    if not db_ok:
        status = "unhealthy"
    elif stats["queue_size"] > stats["queue_capacity"] * 0.9:
        status = "degraded"  # Queue nearly full
    else:
        status = "healthy"
    
    return HealthResponse(
        status=status,
        queue_size=stats["queue_size"],
        queue_capacity=stats["queue_capacity"],
        active_workers=stats["active_workers"],
        database_ok=db_ok
    )


# --- Utility Endpoints ---

@app.post("/admin/cleanup")
async def cleanup_old_requests():
    """Admin endpoint to trigger cleanup of old requests."""
    deleted = await database.cleanup_old_requests()
    return {"deleted": deleted}
