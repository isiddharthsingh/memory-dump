FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Thumbnails and reels are read as these users, one per child (USERS in
# app/sandbox.py): none can read the worker's environment or secrets, or
# another child's work. Their uids are far from the system's (Postgres and
# Redis run as 999) and a host's own users, whose processes would count
# against the same per-user limits.
RUN for i in 0 1 2 3 4 5 6 7; do \
        groupadd --system --gid "6100$i" "sandbox$i" \
        && useradd --system --uid "6100$i" --gid "6100$i" --no-create-home \
            --shell /usr/sbin/nologin "sandbox$i"; \
    done

RUN pip install uv==0.5.7

WORKDIR /app

COPY pyproject.toml ./
# docker-compose.override.yml sets INSTALL_DEV for the api image, so tests,
# ruff and mypy run in it; production images go without.
ARG INSTALL_DEV=false
RUN uv pip install --system -r pyproject.toml \
    && if [ "$INSTALL_DEV" = "true" ]; then \
         uv pip install --system -r pyproject.toml --extra dev; \
       fi

# The Whisper model reels are transcribed with (WHISPER_MODEL in
# app/extractors/reel.py), kept in the image so none waits on a download.
ENV HF_HOME=/opt/huggingface
RUN python -c "import faster_whisper; faster_whisper.download_model('base')"
ENV HF_HUB_OFFLINE=1

COPY . .
# The sandbox users run the children from this code, whatever the checkout's
# permissions were; nothing else here is theirs to read (.dockerignore keeps
# secrets out of the image).
RUN chmod -R a+rX /app/app

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
