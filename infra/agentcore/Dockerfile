# FloodRelay agent runtime image.
#
# NOT BUILT, NOT DEPLOYED. This file has never been run: the Docker daemon was
# not available on the build machine, and there were no AWS credentials to push
# to ECR or launch a runtime with. It is written from the project's actual
# layout and dependency setup, and it is unverified. Expect to fix something the
# first time you build it.
#
# Build from the repository root:
#   docker build -f infra/agentcore/Dockerfile -t floodrelay-agent .
#   docker run --rm -p 8080:8080 --env-file backend/.env floodrelay-agent

FROM python:3.12-slim AS base

# uv gives reproducible installs from the committed lockfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so a code change does not re-resolve the whole tree.
COPY backend/pyproject.toml backend/uv.lock ./backend/
WORKDIR /app/backend
RUN uv sync --frozen --no-install-project --no-dev

COPY backend/src ./src
COPY backend/seed ./seed
RUN uv sync --frozen --no-dev

# Run as a non-root user. The agent reads help requests from the public
# internet; it has no business running as root.
RUN useradd --create-home --uid 10001 floodrelay \
    && chown -R floodrelay:floodrelay /app
USER floodrelay

# AgentCore Runtime expects the container to listen on 8080.
EXPOSE 8080

# In AWS the store is real DynamoDB and media goes to S3, so neither endpoint
# is set here. Everything else comes from the task definition.
ENV DDB_TABLE=floodrelay \
    S3_BUCKET=floodrelay-media \
    AWS_REGION=us-east-1 \
    MODEL_PROVIDER=bedrock \
    DEMO_MODE=false

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "floodrelay.main:app", "--host", "0.0.0.0", "--port", "8080"]
