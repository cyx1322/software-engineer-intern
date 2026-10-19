"""Securities account identity verification: API entry point.

Keep the ASGI entry point importable as `app.main:app` from q1_api/.
"""
import logging

from fastapi import FastAPI

from app import batches, cases, exports

# Uvicorn configures only its own loggers; this sends the app's retry and OCR logs to stderr too.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # failed attempts are logged by app.retry

app = FastAPI(title="Securities Account Identity Verification")
app.include_router(batches.router)
app.include_router(cases.router)
app.include_router(exports.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
