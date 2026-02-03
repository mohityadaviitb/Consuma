"""
Advanced Load Generator with integrated callback timing.

This version:
1. Starts its own callback receiver
2. Tracks time-to-callback precisely
3. Provides comprehensive stats
"""
import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Optional
import threading
import queue

import aiohttp
from aiohttp import web


@dataclass
class Stats:
    """Statistics collector."""
    total: int = 0
    success: int = 0
    failed: int = 0
    latencies_ms: list = field(default_factory=list)
    callback_times_ms: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)
    
    def record_success(self, latency_ms: float):
        self.total += 1
        self.success += 1
        self.latencies_ms.append(latency_ms)
    
    def record_failure(self, error: str):
        self.total += 1
        self.failed += 1
        error_key = error[:50]  # Truncate long errors
        self.errors[error_key] = self.errors.get(error_key, 0) + 1
    
    def record_callback(self, time_ms: float):
        self.callback_times_ms.append(time_ms)
    
    def percentile(self, data: list, p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p / 100
        f = int(k)
        c = min(f + 1, len(sorted_data) - 1)
        return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
    
    def report(self, title: str) -> str:
        lines = [
            f"\n{'='*60}",
            f"  {title}",
            f"{'='*60}",
            f"  Total:      {self.total}",
            f"  Success:    {self.success} ({100*self.success/max(1,self.total):.1f}%)",
            f"  Failed:     {self.failed} ({100*self.failed/max(1,self.total):.1f}%)",
        ]
        
        if self.latencies_ms:
            lines.extend([
                f"\n  Response Latency (ms):",
                f"    p50:  {self.percentile(self.latencies_ms, 50):>10.2f}",
                f"    p95:  {self.percentile(self.latencies_ms, 95):>10.2f}",
                f"    p99:  {self.percentile(self.latencies_ms, 99):>10.2f}",
                f"    min:  {min(self.latencies_ms):>10.2f}",
                f"    max:  {max(self.latencies_ms):>10.2f}",
                f"    avg:  {statistics.mean(self.latencies_ms):>10.2f}",
            ])
        
        if self.callback_times_ms:
            lines.extend([
                f"\n  Time-to-Callback (ms):",
                f"    p50:  {self.percentile(self.callback_times_ms, 50):>10.2f}",
                f"    p95:  {self.percentile(self.callback_times_ms, 95):>10.2f}",
                f"    p99:  {self.percentile(self.callback_times_ms, 99):>10.2f}",
                f"    min:  {min(self.callback_times_ms):>10.2f}",
                f"    max:  {max(self.callback_times_ms):>10.2f}",
                f"    avg:  {statistics.mean(self.callback_times_ms):>10.2f}",
            ])
            
            # Show callback completion rate
            expected = self.success
            received = len(self.callback_times_ms)
            lines.append(f"\n  Callbacks: {received}/{expected} ({100*received/max(1,expected):.1f}%)")
        
        if self.errors:
            lines.append(f"\n  Errors:")
            for err, count in sorted(self.errors.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"    {count:>4}x  {err}")
        
        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


class IntegratedLoadGenerator:
    """
    Load generator with integrated callback server.
    Runs callback receiver in background for precise timing.
    """
    
    def __init__(
        self,
        api_url: str,
        callback_port: int = 9999,
        concurrency: int = 10
    ):
        self.api_url = api_url.rstrip("/")
        self.callback_port = callback_port
        self.callback_url = f"http://localhost:{callback_port}/callback"
        self.concurrency = concurrency
        
        # Tracking
        self.pending: dict[str, float] = {}  # request_id -> send_time
        self.stats = Stats()
        self.callback_server_running = False
        self._runner: Optional[web.AppRunner] = None
    
    async def start_callback_server(self):
        """Start the callback receiver."""
        app = web.Application()
        app.router.add_post("/callback", self._handle_callback)
        
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.callback_port)
        await site.start()
        self.callback_server_running = True
        print(f"   📡 Callback receiver started on port {self.callback_port}")
    
    async def stop_callback_server(self):
        """Stop the callback receiver."""
        if self._runner:
            await self._runner.cleanup()
            self.callback_server_running = False
    
    async def _handle_callback(self, request: web.Request) -> web.Response:
        """Handle incoming callback."""
        receive_time = time.perf_counter()
        
        try:
            data = await request.json()
            request_id = data.get("request_id")
            
            if request_id and request_id in self.pending:
                elapsed_ms = (receive_time - self.pending[request_id]) * 1000
                self.stats.record_callback(elapsed_ms)
                del self.pending[request_id]
            
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def run_sync_test(self, num_requests: int) -> Stats:
        """Run sync endpoint load test."""
        stats = Stats()
        semaphore = asyncio.Semaphore(self.concurrency)
        
        async def send_request(i: int):
            async with semaphore:
                payload = {"data": f"sync-test-{i}-{time.time()}", "iterations": 1000}
                start = time.perf_counter()
                
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"{self.api_url}/sync",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as resp:
                            latency = (time.perf_counter() - start) * 1000
                            if resp.status == 200:
                                stats.record_success(latency)
                            else:
                                stats.record_failure(f"HTTP {resp.status}")
                except Exception as e:
                    stats.record_failure(str(e)[:50])
        
        tasks = [send_request(i) for i in range(num_requests)]
        await asyncio.gather(*tasks)
        
        return stats
    
    async def run_async_test(self, num_requests: int) -> Stats:
        """Run async endpoint load test with callback timing."""
        self.stats = Stats()
        self.pending.clear()
        
        # Start callback server
        await self.start_callback_server()
        
        try:
            semaphore = asyncio.Semaphore(self.concurrency)
            
            async def send_request(i: int):
                async with semaphore:
                    payload = {
                        "data": f"async-test-{i}-{time.time()}",
                        "iterations": 1000,
                        "callback_url": self.callback_url
                    }
                    start = time.perf_counter()
                    
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                f"{self.api_url}/async",
                                json=payload,
                                timeout=aiohttp.ClientTimeout(total=30)
                            ) as resp:
                                latency = (time.perf_counter() - start) * 1000
                                if resp.status == 200:
                                    data = await resp.json()
                                    request_id = data.get("request_id")
                                    if request_id:
                                        self.pending[request_id] = start
                                    self.stats.record_success(latency)
                                else:
                                    self.stats.record_failure(f"HTTP {resp.status}")
                    except Exception as e:
                        self.stats.record_failure(str(e)[:50])
            
            # Send all requests
            tasks = [send_request(i) for i in range(num_requests)]
            await asyncio.gather(*tasks)
            
            # Wait for callbacks
            print(f"\n   ⏳ Waiting for callbacks...")
            wait_time = 0
            max_wait = 30  # seconds
            while len(self.pending) > 0 and wait_time < max_wait:
                await asyncio.sleep(0.5)
                wait_time += 0.5
                remaining = len(self.pending)
                if wait_time % 5 == 0:
                    print(f"      {remaining} callbacks pending...")
            
            if self.pending:
                print(f"   ⚠️  {len(self.pending)} callbacks not received after {max_wait}s")
            
        finally:
            await self.stop_callback_server()
        
        return self.stats


async def main():
    parser = argparse.ArgumentParser(
        description="Load Generator for Sync/Async API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python load_generator_v2.py -n 100 -c 10 --mode both
  python load_generator_v2.py -n 1000 -c 50 --mode sync
  python load_generator_v2.py -n 500 -c 20 --mode async
        """
    )
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("-n", "--requests", type=int, default=100, help="Number of requests")
    parser.add_argument("-c", "--concurrency", type=int, default=10, help="Concurrent requests")
    parser.add_argument("--mode", choices=["sync", "async", "both"], default="both")
    parser.add_argument("--callback-port", type=int, default=9999, help="Port for callback receiver")
    
    args = parser.parse_args()
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║                    LOAD GENERATOR                        ║
╠══════════════════════════════════════════════════════════╣
║  Target:      {args.url:<42} ║
║  Requests:    {args.requests:<42} ║
║  Concurrency: {args.concurrency:<42} ║
║  Mode:        {args.mode:<42} ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    generator = IntegratedLoadGenerator(
        api_url=args.url,
        callback_port=args.callback_port,
        concurrency=args.concurrency
    )
    
    if args.mode in ("sync", "both"):
        print("🔄 Running SYNC load test...")
        stats = await generator.run_sync_test(args.requests)
        print(stats.report("SYNC ENDPOINT RESULTS"))
    
    if args.mode in ("async", "both"):
        print("🔄 Running ASYNC load test...")
        stats = await generator.run_async_test(args.requests)
        print(stats.report("ASYNC ENDPOINT RESULTS"))
    
    print("✅ Load test complete!")


if __name__ == "__main__":
    asyncio.run(main())
