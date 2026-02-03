"""
Configuration with sensible defaults and environment overrides.
"""
from pydantic_settings import BaseSettings                 #1
from typing import Set                  #2
import ipaddress
                  

class Settings(BaseSettings):
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./requests.db"                    #3
    
    # Async worker settings
    async_worker_count: int = 10  # Number of concurrent callback workers
    async_queue_max_size: int = 10000  # Max pending async jobs
    
    # Callback settings
    callback_timeout_seconds: float = 10.0
    callback_max_retries: int = 3
    callback_retry_base_delay: float = 1.0  # Exponential backoff base                 #4
    callback_retry_max_delay: float = 30.0
    
    # SSRF protection - block internal/private IPs
    callback_block_private_ips: bool = True
    callback_allowed_schemes: Set[str] = {"http", "https"}
    
    # Request storage limits (prevent unbounded growth)
    max_stored_requests: int = 100000
    request_retention_hours: int = 24
    
    # Work simulation
    work_duration_seconds: float = 0.1  # Simulated work duration
    
    class Config:
        env_prefix = "APP_"


settings = Settings()


def is_private_ip(hostname: str) -> bool:
    """Check if hostname resolves to a private/internal IP (SSRF protection)."""
    try:
        import socket
        # Resolve hostname to IP
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        
        # Block private, loopback, link-local, reserved ranges
        return (
            ip.is_private or
            ip.is_loopback or
            ip.is_link_local or
            ip.is_reserved or
            ip.is_multicast
        )
    except (socket.gaierror, ValueError):
        # If we can't resolve, block it to be safe
        return True
