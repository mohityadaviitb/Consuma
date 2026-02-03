"""
Unit tests for the Sync vs Async API.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app import database


@pytest_asyncio.fixture
async def client():
    """Create async test client."""
    # Initialize database
    await database.init_database()
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_sync_endpoint(client):
    """Test POST /sync returns correct response."""
    response = await client.post("/sync", json={
        "data": "test data",
        "iterations": 100
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "result" in data
    assert data["result"]["iterations"] == 100
    assert "input_hash" in data["result"]
    assert "output_hash" in data["result"]
    assert "processing_time_ms" in data["result"]


@pytest.mark.asyncio
async def test_sync_validation_error(client):
    """Test POST /sync with invalid payload."""
    # Empty data
    response = await client.post("/sync", json={
        "data": "",
        "iterations": 100
    })
    assert response.status_code == 422  # Validation error
    
    # Iterations too high
    response = await client.post("/sync", json={
        "data": "test",
        "iterations": 2000000
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_async_endpoint(client):
    """Test POST /async returns acknowledgment."""
    response = await client.post("/async", json={
        "data": "test data",
        "iterations": 100,
        "callback_url": "http://example.com/callback"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert "request_id" in data


@pytest.mark.asyncio
async def test_async_invalid_callback_url(client):
    """Test POST /async with invalid callback URL."""
    response = await client.post("/async", json={
        "data": "test",
        "iterations": 100,
        "callback_url": "ftp://invalid.com/callback"  # Invalid scheme
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_health_check(client):
    """Test GET /healthz returns health status."""
    response = await client.get("/healthz")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded", "unhealthy")
    assert "queue_size" in data
    assert "queue_capacity" in data
    assert "database_ok" in data


@pytest.mark.asyncio
async def test_list_requests(client):
    """Test GET /requests returns list."""
    # Create a request first
    await client.post("/sync", json={"data": "test", "iterations": 10})
    
    response = await client.get("/requests")
    
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "requests" in data
    assert len(data["requests"]) > 0


@pytest.mark.asyncio
async def test_list_requests_filter_by_mode(client):
    """Test GET /requests with mode filter."""
    response = await client.get("/requests?mode=sync")
    
    assert response.status_code == 200
    data = response.json()
    for req in data["requests"]:
        assert req["mode"] == "sync"


@pytest.mark.asyncio
async def test_get_request_not_found(client):
    """Test GET /requests/{id} with non-existent ID."""
    response = await client.get("/requests/non-existent-id")
    
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deterministic_work():
    """Test that work produces deterministic results."""
    from app.work import compute_work_sync
    from app.models import WorkPayload
    
    payload = WorkPayload(data="test input", iterations=100)
    
    result1 = compute_work_sync("req1", payload)
    result2 = compute_work_sync("req2", payload)
    
    # Same input should produce same output hash
    assert result1.input_hash == result2.input_hash
    assert result1.output_hash == result2.output_hash


@pytest.mark.asyncio
async def test_ssrf_protection():
    """Test SSRF protection blocks internal IPs."""
    from app.config import is_private_ip
    
    # Should block
    assert is_private_ip("127.0.0.1") == True
    assert is_private_ip("localhost") == True
    assert is_private_ip("192.168.1.1") == True
    assert is_private_ip("10.0.0.1") == True
    assert is_private_ip("172.16.0.1") == True
    
    # Should allow (external IPs)
    # Note: These might fail if DNS lookup fails
    # assert is_private_ip("8.8.8.8") == False
