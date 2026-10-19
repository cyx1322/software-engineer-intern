# API image (Compose service `api`). The build context is the repository root.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /srv

# Dependencies first: this layer is rebuilt only when requirements.txt changes,
# not on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --system --uid 10001 --no-create-home app

COPY data/account_applications.csv ./data/
COPY q1_api/app ./q1_api/app

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "q1_api", "--host", "0.0.0.0", "--port", "8000"]
