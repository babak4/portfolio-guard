# Portfolio Guard image. Secrets are never copied in: they arrive at runtime through env_file.
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 TZ=Europe/London
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --extra dashboard --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --extra dashboard --no-dev
ENV PATH="/app/.venv/bin:$PATH" GUARD_DATA_DIR=/data
RUN useradd --create-home --uid 10001 guard && mkdir /data && chown guard /data
USER guard
VOLUME ["/data"]
CMD ["python", "-m", "guard.worker"]
