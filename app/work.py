"""
Shared work logic - used by both sync and async paths.
This is the core business logic that MUST be identical for both modes.
"""
import hashlib
import time
import asyncio
from app.models import WorkPayload, WorkResult


def compute_work_sync(request_id: str, payload: WorkPayload) -> WorkResult:
    """
    Perform deterministic work on the payload.
    
    This simulates real work by:
    1. Computing input hash
    2. Iteratively hashing (CPU-bound simulation)
    3. Returning deterministic result
    
    The work is intentionally deterministic - same input = same output.
    """
    start_time = time.perf_counter()
    
    # Step 1: Hash the input
    input_hash = hashlib.sha256(payload.data.encode()).hexdigest()
    
    # Step 2: Iterative hashing (simulates CPU work)
    current_hash = input_hash
    for i in range(payload.iterations):
        current_hash = hashlib.sha256(
            f"{current_hash}:{i}".encode()
        ).hexdigest()
    
    output_hash = current_hash
    
    # Calculate processing time
    processing_time_ms = (time.perf_counter() - start_time) * 1000
    
    return WorkResult(
        request_id=request_id,
        input_hash=input_hash,
        output_hash=output_hash,
        iterations=payload.iterations,
        processing_time_ms=round(processing_time_ms, 3)
    )


async def compute_work_async(request_id: str, payload: WorkPayload) -> WorkResult:
    """
    Async wrapper for work computation.
    Runs CPU-bound work in thread pool to not block event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,  # Default thread pool
        compute_work_sync,
        request_id,
        payload
    )
