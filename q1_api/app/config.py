"""Settings read from environment variables when the app starts.

The defaults match the no-Docker setup in q2_integration/README.md.
"""
import os

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "identity_verification")
SEED_CSV = os.getenv("SEED_CSV")  # imported once at startup when set; Compose sets it
READY_TIMEOUT_SECONDS = float(os.getenv("READY_TIMEOUT_SECONDS", "2"))

OCR_URL = os.getenv("OCR_URL", "http://localhost:9100/ocr/parse")
OCR_TIMEOUT_SECONDS = float(os.getenv("OCR_TIMEOUT_SECONDS", "5"))
OCR_MAX_ATTEMPTS = int(os.getenv("OCR_MAX_ATTEMPTS", "6"))
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "4"))

ONBOARDING_URL = os.getenv("ONBOARDING_URL", "http://localhost:9200/ingest")
ONBOARDING_TIMEOUT_SECONDS = float(os.getenv("ONBOARDING_TIMEOUT_SECONDS", "5"))
ONBOARDING_MAX_ATTEMPTS = int(os.getenv("ONBOARDING_MAX_ATTEMPTS", "6"))

# The wait before retry n is BASE * 2**(n-1), capped at MAX, then scaled by a random 50-100%.
RETRY_BACKOFF_BASE_SECONDS = float(os.getenv("RETRY_BACKOFF_BASE_SECONDS", "0.5"))
RETRY_BACKOFF_MAX_SECONDS = float(os.getenv("RETRY_BACKOFF_MAX_SECONDS", "8"))
