# Sync vs Async API Backend

A Python backend demonstrating synchronous and asynchronous (callback-based) request patterns under load, with comprehensive handling of edge cases.

## Quick Start

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the API server
python -m uvicorn app.main:app --reload --port 8000

# 4. (Optional) In another terminal, run load tests
python load_generator_v2.py -n 100 -c 10 --mode both
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sync` | POST | Synchronous work - returns result inline |
| `/async` | POST | Async work - returns ack, delivers result to callback_url |
| `/requests` | GET | List recent requests (filter: `?mode=sync\|async`) |
| `/requests/{id}` | GET | Get specific request details + status |
| `/healthz` | GET | Health check with queue/worker stats |

### Example: Sync Request

```bash
curl -X POST http://localhost:8000/sync \
  -H "Content-Type: application/json" \
  -d '{"data": "hello world", "iterations": 1000}'
```

### Example: Async Request

```bash
curl -X POST http://localhost:8000/async \
  -H "Content-Type: application/json" \
  -d '{
    "data": "hello world",
    "iterations": 1000,
    "callback_url": "http://localhost:8001/callback"
  }'
```

## Load Generator

The included load generator tests both endpoints and reports latency percentiles:

```bash
# Test both endpoints with 100 requests, 10 concurrent
python load_generator_v2.py -n 100 -c 10 --mode both

# Heavy sync test
python load_generator_v2.py -n 1000 -c 50 --mode sync

# Async test with callback timing
python load_generator_v2.py -n 500 -c 20 --mode async
```

**Output includes:**
- Total requests sent, success/failure counts
- Response latency: p50, p95, p99, min, max, avg
- Time-to-callback stats for async requests

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI Server                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  POST /sync ──────► compute_work() ──────► Response (inline)    │
│                           │                                     │
│                           │ (shared logic)                      │
│                           │                                     │
│  POST /async ──────► Queue ──────► Worker Pool ──────► Callback │
│       │                              │                          │
│       └──► Immediate ACK             └──► Retry with backoff    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                     SQLite Database                             │
│   (request tracking, status, callback delivery audit)          │
└─────────────────────────────────────────────────────────────────┘
```

## Design Decisions & Tradeoffs

### 1. Shared Work Logic
- **Decision**: Single `compute_work()` function used by both sync and async paths
- **Why**: Eliminates business logic duplication, ensures identical behavior
- **Location**: [app/work.py](app/work.py)

### 2. Callback Failure Handling
- **Decision**: Exponential backoff with jitter, max 3 retries
- **Config**: `callback_max_retries=3`, `callback_retry_base_delay=1.0s`
- **Status tracking**: `CALLBACK_PENDING` → `CALLBACK_SUCCESS` or `CALLBACK_FAILED`
- **Tradeoff**: More retries = higher delivery rate but increased latency for failing callbacks

### 3. SSRF Protection
- **Decision**: Block private/internal IP ranges in callback URLs
- **Checks**: 
  - Only `http://` and `https://` schemes allowed
  - Blocks `localhost`, `127.0.0.1`, `::1`, `0.0.0.0`
  - Blocks RFC 1918 private ranges (10.x, 172.16-31.x, 192.168.x)
  - No redirect following (prevents redirect-based SSRF)
- **Location**: [app/callback_worker.py](app/callback_worker.py) `_is_safe_callback_url()`

### 4. Scaling Under Load
- **Decision**: Bounded async queue with configurable workers
- **Config**: `async_queue_max_size=10000`, `async_worker_count=10`
- **Backpressure**: Returns 503 when queue is full (client should retry)
- **Tradeoff**: Fixed worker count limits parallelism but prevents resource exhaustion

### 5. Request Ordering
- **Decision**: FIFO queue processing, but no strict ordering guarantee
- **Why**: Workers process in parallel, so requests may complete out of order
- **Guarantee**: Each individual request will be processed exactly once
- **Enhancement option**: Add request priority or sequence numbers if strict ordering needed

### 6. Persistence
- **Decision**: SQLite with async driver (aiosqlite)
- **Why**: Simple setup, good for demo/dev, easy to swap for PostgreSQL
- **Retention**: Configurable cleanup (default 24h)
- **Tradeoff**: Single-file DB limits write concurrency; production should use PostgreSQL

### 7. CPU-bound Work Handling
- **Decision**: Run compute in thread pool via `run_in_executor()`
- **Why**: Prevents blocking the async event loop during hash iterations
- **Location**: [app/work.py](app/work.py) `compute_work_async()`

## Configuration

Environment variables (prefix with `APP_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ASYNC_WORKER_COUNT` | 10 | Number of callback workers |
| `APP_ASYNC_QUEUE_MAX_SIZE` | 10000 | Max pending async jobs |
| `APP_CALLBACK_TIMEOUT_SECONDS` | 10 | Callback HTTP timeout |
| `APP_CALLBACK_MAX_RETRIES` | 3 | Retry attempts for failed callbacks |
| `APP_CALLBACK_BLOCK_PRIVATE_IPS` | true | Enable SSRF protection |
| `APP_WORK_DURATION_SECONDS` | 0.1 | Base work duration |

## Gotchas & Edge Cases Handled

| Issue | Solution |
|-------|----------|
| Callback URL is down | Retry with exponential backoff, track attempts |
| Callback URL is internal (SSRF) | Block private IPs, localhost, no redirects |
| Queue overwhelmed | Bounded queue with 503 response (backpressure) |
| Duplicate business logic | Single `compute_work()` function |
| Event loop blocked | CPU work runs in thread pool |
| Request tracking | SQLite persistence with indexes |
| Graceful shutdown | Worker pool cleanup on SIGTERM |
| Memory exhaustion | Request retention + cleanup job |
| Input validation | Pydantic models with constraints |

## Project Structure

```
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI app & endpoints
│   ├── config.py         # Settings & SSRF helpers
│   ├── models.py         # Pydantic request/response models
│   ├── work.py           # Shared work logic
│   ├── database.py       # SQLite persistence
│   └── callback_worker.py # Async worker pool
├── load_generator.py     # Basic load generator
├── load_generator_v2.py  # Advanced with integrated callback server
├── callback_server.py    # Standalone mock callback receiver
├── requirements.txt
└── README.md
```

## Testing Scenarios

### 1. Basic functionality
```bash
# Sync request
curl -X POST http://localhost:8000/sync \
  -H "Content-Type: application/json" \
  -d '{"data": "test", "iterations": 100}'

# Check request was stored
curl http://localhost:8000/requests?limit=5
```

### 2. Async with callback
```bash
# Terminal 1: Start callback receiver
python callback_server.py

# Terminal 2: Send async request
curl -X POST http://localhost:8000/async \
  -H "Content-Type: application/json" \
  -d '{"data": "test", "iterations": 100, "callback_url": "http://localhost:8001/callback"}'

# Check callback was received
curl http://localhost:8001/callbacks
```

### 3. SSRF protection
```bash
# This should fail (blocked)
curl -X POST http://localhost:8000/async \
  -H "Content-Type: application/json" \
  -d '{"data": "test", "iterations": 100, "callback_url": "http://localhost:8000/sync"}'
```

### 4. Load test
```bash
python load_generator_v2.py -n 500 -c 50 --mode both
```


### 5.Load Generator Commands
# Basic test (20 requests, 5 concurrent, both endpoints)
python load_generator.py -n 20 -c 5 --mode both

# Heavy sync test
python load_generator.py -n 500 -c 50 --mode sync

# Async only test
python load_generator.py -n 100 -c 20 --mode async

# Custom URL
python load_generator.py --url http://localhost:8000 -n 50 -c 10 


### 6.Unit Tests Commands
# Run all tests
python -m pytest tests/ -v

# Run specific test
python -m pytest tests/test_api.py::test_sync_endpoint -v

# Run with coverage (need to install pytest-cov first)
python -m pytest tests/ --cov=app

### 7.Start Server for Testing
# Normal (with SSRF protection - blocks localhost callbacks)
python -m uvicorn app.main:app --port 8000

# For local testing (disable SSRF to allow localhost callbacks)
APP_CALLBACK_BLOCK_PRIVATE_IPS=false python -m uvicorn app.main:app --port 8000








