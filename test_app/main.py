import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("loglm-test-app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LogLM test app starting up")
    yield
    logger.info("LogLM test app shutting down")


app = FastAPI(title="LogLM Test App", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FAKE_USERS = {str(i): {"id": str(i), "name": f"User {i}", "email": f"user{i}@example.com"} for i in range(1, 21)}


# Middleware: log every request with latency
@app.middleware("http")
async def request_logger(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.time()

    response = await call_next(request)

    latency_ms = round((time.time() - start) * 1000, 2)
    status = response.status_code

    log = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "latency_ms": latency_ms,
        "client_ip": request.client.host if request.client else "unknown",
    }

    if status >= 500:
        logger.error(f"Request failed: {log}")
    elif status >= 400:
        logger.warning(f"Client error: {log}")
    elif latency_ms > 2000:
        logger.warning(f"Slow request: {log}")
    else:
        logger.info(f"Request completed: {log}")

    return response


# --- Stable endpoints ---

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/")
async def root():
    return {"service": "loglm-test-app", "version": "1.0.0"}


# --- HTTP error simulation ---

@app.get("/api/users")
async def list_users():
    """Returns user list. Occasionally fails with 503."""
    if random.random() < 0.15:
        logger.error("Database connection pool exhausted while fetching users")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable — DB pool exhausted")

    if random.random() < 0.08:
        logger.error("Unexpected error in list_users: internal serialization failure")
        raise HTTPException(status_code=500, detail="Internal server error")

    logger.info(f"Fetched {len(FAKE_USERS)} users from database")
    return {"users": list(FAKE_USERS.values()), "total": len(FAKE_USERS)}


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    """Fetch a single user. Returns 404 for unknown IDs."""
    if user_id not in FAKE_USERS:
        logger.warning(f"User not found: user_id={user_id}")
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")

    if random.random() < 0.1:
        logger.error(f"Permission check service timed out for user_id={user_id}")
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    return FAKE_USERS[user_id]


@app.post("/api/data")
async def process_data(request: Request):
    """Accepts arbitrary JSON. Randomly rejects or errors."""
    try:
        body = await request.json()
    except Exception:
        logger.warning("Received malformed JSON payload")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not body:
        logger.warning("Empty payload received in /api/data")
        raise HTTPException(status_code=422, detail="Payload must not be empty")

    if random.random() < 0.2:
        logger.error(f"Processing pipeline failed for payload size={len(str(body))} bytes")
        raise HTTPException(status_code=500, detail="Processing pipeline error")

    logger.info(f"Processed data payload: keys={list(body.keys()) if isinstance(body, dict) else 'non-dict'}")
    return {"status": "accepted", "keys_received": list(body.keys()) if isinstance(body, dict) else None}


@app.delete("/api/resource/{resource_id}")
async def delete_resource(resource_id: str, request: Request):
    """Simulates auth-gated delete — frequently unauthorized."""
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        logger.warning(f"Delete attempt without auth token: resource_id={resource_id} ip={request.client.host}")
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.split(" ", 1)[1]
    if len(token) < 20 or not token.isalnum():
        logger.warning(f"Invalid token format on delete: resource_id={resource_id} token_len={len(token)}")
        raise HTTPException(status_code=403, detail="Token validation failed")

    if random.random() < 0.25:
        logger.error(f"Idempotency check failed: resource_id={resource_id} already deleted or not found")
        raise HTTPException(status_code=404, detail="Resource not found or already deleted")

    logger.info(f"Resource deleted: resource_id={resource_id}")
    return {"deleted": resource_id}


# --- Performance degradation simulation ---

@app.get("/api/slow-query")
async def slow_query():
    """Simulates a slow database query."""
    delay = random.uniform(1.5, 5.0)
    logger.warning(f"Slow query initiated, estimated wait={delay:.2f}s")
    await asyncio.sleep(delay)

    if random.random() < 0.2:
        logger.error(f"Query timed out after {delay:.2f}s — no results returned")
        raise HTTPException(status_code=504, detail="Query timeout")

    logger.info(f"Slow query completed in {delay:.2f}s")
    return {"rows": random.randint(0, 500), "query_time_s": round(delay, 2)}


@app.get("/api/unstable")
async def unstable_endpoint():
    """Randomly degrades: adds latency and sometimes fails."""
    delay = random.choices(
        [0, random.uniform(0.1, 0.5), random.uniform(1.0, 3.0), random.uniform(3.0, 6.0)],
        weights=[0.5, 0.25, 0.15, 0.10],
    )[0]

    if delay > 0:
        logger.warning(f"Unstable endpoint: injecting {delay:.2f}s delay")
        await asyncio.sleep(delay)

    outcome = random.choices(
        ["ok", "bad_request", "server_error", "overload"],
        weights=[0.55, 0.15, 0.20, 0.10],
    )[0]

    if outcome == "bad_request":
        logger.warning("Unstable endpoint returned 400: validation inconsistency")
        raise HTTPException(status_code=400, detail="Validation inconsistency — upstream data malformed")

    if outcome == "server_error":
        logger.error("Unstable endpoint returned 500: downstream dependency failed")
        raise HTTPException(status_code=500, detail="Downstream dependency failure")

    if outcome == "overload":
        logger.error("Unstable endpoint returned 503: rate limit exceeded")
        raise HTTPException(status_code=503, detail="Rate limit exceeded — try again later")

    return {"status": "ok", "latency_injected_s": round(delay, 2)}


@app.get("/api/memory-leak-sim")
async def memory_leak_sim():
    """Allocates a large chunk and logs the warning — simulates a gradual memory issue."""
    chunk_size_mb = random.randint(10, 80)
    chunk = bytearray(chunk_size_mb * 1024 * 1024)
    logger.warning(f"High memory allocation detected: {chunk_size_mb}MB allocated in single request")
    del chunk
    return {"allocated_mb": chunk_size_mb, "status": "released"}


@app.get("/api/cascade-failure")
async def cascade_failure():
    """Simulates a cascade: slow primary + failing fallback."""
    logger.warning("Primary service timeout — attempting fallback")
    await asyncio.sleep(random.uniform(2.0, 4.0))

    if random.random() < 0.6:
        logger.error("Fallback service also unavailable — cascade failure detected")
        raise HTTPException(status_code=503, detail="All upstream services unavailable")

    logger.warning("Fallback service responded (degraded mode)")
    return {"mode": "degraded", "source": "fallback", "data_stale": True}


# --- Batch traffic generator (call this to flood logs quickly) ---

@app.post("/api/load-test/run")
async def run_load_test(request: Request):
    """Fires N internal requests concurrently to generate log volume."""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    n = min(int(body.get("requests", 20)), 100)

    logger.info(f"Load test started: {n} concurrent synthetic requests")

    endpoints = ["/api/users", "/api/slow-query", "/api/unstable", "/api/users/99"]
    results = {"ok": 0, "error": 0}

    async def hit(path):
        try:
            delay = random.uniform(0.05, 2.0)
            await asyncio.sleep(delay)
            if random.random() < 0.3:
                results["error"] += 1
                logger.error(f"Synthetic request to {path} failed after {delay:.2f}s")
            else:
                results["ok"] += 1
                logger.info(f"Synthetic request to {path} completed in {delay:.2f}s")
        except Exception as e:
            results["error"] += 1
            logger.error(f"Unexpected error in synthetic request to {path}: {e}")

    tasks = [hit(random.choice(endpoints)) for _ in range(n)]
    await asyncio.gather(*tasks)

    logger.info(f"Load test finished: {results}")
    return {"requests_fired": n, "results": results}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
