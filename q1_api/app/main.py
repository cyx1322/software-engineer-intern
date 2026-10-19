"""Securities account identity verification: API entry point.

Keep the ASGI entry point importable as `app.main:app` from q1_api/.
"""
import logging
from contextlib import asynccontextmanager

import pymongo
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app import batches, cases, config, exports
from app.db import get_db

# Uvicorn configures only its own loggers; this sends the app's retry and OCR logs to stderr too.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # failed attempts are logged by app.retry
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.SEED_CSV:
        batches.seed_batch(get_db(), config.SEED_CSV)
    yield


app = FastAPI(title="Securities Account Identity Verification", lifespan=lifespan)
app.include_router(batches.router)
app.include_router(cases.router)
app.include_router(exports.router)


@app.get("/api/health")
def health():
    """Liveness: the process is serving requests. It does not touch the database."""
    return {"status": "ok"}


@app.get("/api/ready", responses={503: {"description": "The database is unreachable"}})
def ready():
    """Readiness: ready only while a round trip to MongoDB succeeds within READY_TIMEOUT_SECONDS."""
    try:
        with pymongo.timeout(config.READY_TIMEOUT_SECONDS):
            get_db().command("ping")  # MongoDB's equivalent of SELECT 1
    except PyMongoError as exc:
        log.warning("not ready: database unreachable: %r", exc)
        return JSONResponse({"status": "not ready", "reason": "database unreachable"}, status_code=503)
    return {"status": "ready"}
