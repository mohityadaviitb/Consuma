"""
Mock Callback Server for testing async endpoint.

This server:
1. Receives callbacks from the main API
2. Records timing for callback latency stats
3. Provides endpoints to query received callbacks
"""
import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Callback Receiver", description="Mock server to receive async callbacks")

# Storage for received callbacks
callbacks_received: list[dict] = []
callback_times: dict[str, float] = {}  # request_id -> receive timestamp


@app.post("/callback")
async def receive_callback(request: Request):
    """Receive callback from async API."""
    receive_time = time.perf_counter()
    
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    
    request_id = data.get("request_id", "unknown")
    
    # Store callback
    callbacks_received.append({
        "received_at": datetime.utcnow().isoformat(),
        "request_id": request_id,
        "data": data
    })
    callback_times[request_id] = receive_time
    
    print(f"✅ Received callback for request {request_id}")
    
    return {"status": "received", "request_id": request_id}


@app.get("/callbacks")
async def list_callbacks(limit: int = 100):
    """List received callbacks."""
    return {
        "total": len(callbacks_received),
        "callbacks": callbacks_received[-limit:]
    }


@app.get("/callbacks/{request_id}")
async def get_callback(request_id: str):
    """Get a specific callback by request ID."""
    for cb in callbacks_received:
        if cb["request_id"] == request_id:
            return cb
    return JSONResponse(status_code=404, content={"error": "Not found"})


@app.delete("/callbacks")
async def clear_callbacks():
    """Clear all stored callbacks."""
    global callbacks_received, callback_times
    count = len(callbacks_received)
    callbacks_received = []
    callback_times = {}
    return {"cleared": count}


@app.get("/stats")
async def get_stats():
    """Get callback statistics."""
    return {
        "total_received": len(callbacks_received),
        "unique_requests": len(set(cb["request_id"] for cb in callbacks_received))
    }


if __name__ == "__main__":
    print("🎯 Starting Callback Receiver on port 8001...")
    print("   Callbacks will be received at: http://localhost:8001/callback")
    print("   View received callbacks at: http://localhost:8001/callbacks")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="warning")
