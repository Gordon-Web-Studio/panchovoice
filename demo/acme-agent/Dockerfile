# ACME Appointments demo voice agent (pv-ws/1 on port 8710)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface \
    ACME_HOST=0.0.0.0 \
    ACME_PORT=8710

# Dependencies first, for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8710
VOLUME ["/cache"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8710/health', timeout=2)" || exit 1

CMD ["uv", "run", "--no-sync", "acme-agent"]
