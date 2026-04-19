FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY .codex-plugin ./.codex-plugin
COPY docs ./docs
COPY scripts ./scripts
COPY data/.gitkeep ./data/.gitkeep

RUN python -m pip install --upgrade pip && \
    pip install -e . pytest

CMD ["bash", "scripts/docker-e2e.sh"]
