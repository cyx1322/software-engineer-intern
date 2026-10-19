"""Securities account identity verification: API entry point.

Keep the ASGI entry point importable as `app.main:app` from q1_api/.
"""
from fastapi import FastAPI

from app import batches, cases

app = FastAPI(title="Securities Account Identity Verification")
app.include_router(batches.router)
app.include_router(cases.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
