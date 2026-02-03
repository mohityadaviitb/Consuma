"""
Async callback worker system.

Handles:
- Background task queue for async requests
- Callback delivery with retry logic
- SSRF protection for callback URLs
- Graceful shutdown
"""
import asyncio
import httpx
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
import random

from app.config import settings, is_private_ip
from app.models import (
    AsyncWorkPayload, WorkPayload, WorkResult, 
    RequestRecord, RequestMode, RequestStatus, CallbackPayload
)
from app.work import compute_work_async
from app import database

logger = logging.getLogger(__name__)


class CallbackWorkerPool:
    """
    Manages async request processing and callback delivery.
    
    Design decisions:
    - Uses asyncio.Queue for backpressure (bounded queue)
    - Multiple workers for parallelism
    - Exponential backoff for retry
    - SSRF protection via URL validation
    """
    
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.async_queue_max_size
        )
        self.workers: list[asyncio.Task] = []
        self.running = False
        self.http_client: Optional[httpx.AsyncClient] = None
        self._active_workers = 0
    
    async def start(self):
        """Start the worker pool."""
        if self.running:
            return
        
        self.running = True
        self.http_client = httpx.AsyncClient(
            timeout=settings.callback_timeout_seconds,
            follow_redirects=False,  # Security: don't follow redirects (SSRF)
            limits=httpx.Limits(max_connections=100)
        )
        
        # Start worker tasks
        for i in range(settings.async_worker_count):
            task = asyncio.create_task(
                self._worker(i),
                name=f"callback-worker-{i}"
            )
            self.workers.append(task)
        
        logger.info(f"Started {settings.async_worker_count} callback workers")
    
    async def stop(self):
        """Gracefully stop the worker pool."""
        if not self.running:
            return
        
        self.running = False
        
        # Cancel all workers
        for worker in self.workers:
            worker.cancel()
        
        # Wait for workers to finish
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        
        # Close HTTP client
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        logger.info("Callback worker pool stopped")
    
    async def enqueue(self, request_id: str, payload: AsyncWorkPayload) -> bool:
        """
        Add async request to queue.
        Returns False if queue is full (backpressure).
        """
        try:
            self.queue.put_nowait((request_id, payload))
            return True
        except asyncio.QueueFull:
            logger.warning(f"Queue full, rejecting request {request_id}")
            return False
    
    def get_stats(self) -> dict:
        """Get queue statistics."""
        return {
            "queue_size": self.queue.qsize(),
            "queue_capacity": settings.async_queue_max_size,
            "active_workers": self._active_workers,
            "total_workers": len(self.workers)
        }
    
    async def _worker(self, worker_id: int):
        """Worker loop - processes requests from queue."""
        logger.debug(f"Worker {worker_id} started")
        
        while self.running:
            try:
                # Get next request (with timeout for graceful shutdown)
                try:
                    request_id, payload = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                self._active_workers += 1
                try:
                    await self._process_request(request_id, payload)
                finally:
                    self._active_workers -= 1
                    self.queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Worker {worker_id} error: {e}")
        
        logger.debug(f"Worker {worker_id} stopped")
    
    async def _process_request(self, request_id: str, payload: AsyncWorkPayload):
        """Process a single async request."""
        logger.debug(f"Processing async request {request_id}")
        
        # Update status to processing
        record = await database.get_request(request_id)
        if record:
            record.status = RequestStatus.PROCESSING
            await database.save_request(record)
        
        try:
            # Perform the work
            work_payload = WorkPayload(
                data=payload.data,
                iterations=payload.iterations
            )
            result = await compute_work_async(request_id, work_payload)
            
            # Update status
            if record:
                record.status = RequestStatus.CALLBACK_PENDING
                record.result = result
                record.completed_at = datetime.utcnow()
                await database.save_request(record)
            
            # Deliver callback
            await self._deliver_callback(request_id, payload.callback_url, result)
            
        except Exception as e:
            logger.exception(f"Error processing request {request_id}: {e}")
            if record:
                record.status = RequestStatus.CALLBACK_FAILED
                record.callback_last_error = str(e)
                await database.save_request(record)
    
    async def _deliver_callback(
        self, 
        request_id: str, 
        callback_url: str, 
        result: WorkResult
    ):
        """Deliver result to callback URL with retry."""
        
        # Validate callback URL (SSRF protection)
        if not self._is_safe_callback_url(callback_url):
            logger.warning(f"Blocked unsafe callback URL for {request_id}: {callback_url}")
            await self._update_callback_status(
                request_id, 
                success=False,
                error="Callback URL blocked by security policy"
            )
            return
        
        # Prepare callback payload
        callback_payload = CallbackPayload(
            request_id=request_id,
            status="success",
            result=result,
            timestamp=datetime.utcnow()
        )
        
        # Retry loop with exponential backoff
        last_error = None
        for attempt in range(settings.callback_max_retries + 1):
            try:
                response = await self.http_client.post(
                    callback_url,
                    json=callback_payload.model_dump(mode='json'),
                    headers={
                        "Content-Type": "application/json",
                        "X-Request-ID": request_id,
                        "X-Attempt": str(attempt + 1)
                    }
                )
                
                # Accept any 2xx response as success
                if 200 <= response.status_code < 300:
                    logger.info(f"Callback delivered for {request_id}")
                    await self._update_callback_status(request_id, success=True)
                    return
                else:
                    last_error = f"HTTP {response.status_code}"
                    logger.warning(
                        f"Callback failed for {request_id} (attempt {attempt + 1}): {last_error}"
                    )
                    
            except httpx.TimeoutException:
                last_error = "Timeout"
                logger.warning(
                    f"Callback timeout for {request_id} (attempt {attempt + 1})"
                )
            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning(
                    f"Callback error for {request_id} (attempt {attempt + 1}): {e}"
                )
            
            # Update attempt count
            await self._increment_callback_attempt(request_id, last_error)
            
            # Exponential backoff with jitter
            if attempt < settings.callback_max_retries:
                delay = min(
                    settings.callback_retry_base_delay * (2 ** attempt),
                    settings.callback_retry_max_delay
                )
                delay *= (0.5 + random.random())  # Add jitter
                await asyncio.sleep(delay)
        
        # All retries exhausted
        logger.error(f"Callback permanently failed for {request_id}: {last_error}")
        await self._update_callback_status(
            request_id, 
            success=False, 
            error=f"Max retries exceeded: {last_error}"
        )
    
    def _is_safe_callback_url(self, url: str) -> bool:
        """
        Validate callback URL is safe (SSRF protection).
        
        Blocks:
        - Non-HTTP(S) schemes
        - Private/internal IP addresses
        - Localhost variants
        """
        try:
            parsed = urlparse(url)
            
            # Check scheme
            if parsed.scheme not in settings.callback_allowed_schemes:
                return False
            
            # Check for private IPs if enabled
            if settings.callback_block_private_ips:
                hostname = parsed.hostname or ""
                
                # Block localhost variants
                if hostname.lower() in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
                    return False
                
                # Block private IP ranges
                if is_private_ip(hostname):
                    return False
            
            return True
            
        except Exception:
            return False
    
    async def _update_callback_status(
        self, 
        request_id: str, 
        success: bool, 
        error: Optional[str] = None
    ):
        """Update request status after callback attempt."""
        record = await database.get_request(request_id)
        if record:
            record.status = (
                RequestStatus.CALLBACK_SUCCESS if success 
                else RequestStatus.CALLBACK_FAILED
            )
            if error:
                record.callback_last_error = error
            await database.save_request(record)
    
    async def _increment_callback_attempt(
        self, 
        request_id: str, 
        error: str
    ):
        """Increment callback attempt counter."""
        record = await database.get_request(request_id)
        if record:
            record.callback_attempts += 1
            record.callback_last_error = error
            await database.save_request(record)


# Global worker pool instance
worker_pool = CallbackWorkerPool()
