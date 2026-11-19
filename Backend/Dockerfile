FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY pyproject.toml README.md ./
COPY research_assistant ./research_assistant
COPY evals ./evals
RUN pip install --no-cache-dir '.[postgres]'

USER app:app
EXPOSE 8000
CMD ["uvicorn", "research_assistant.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
