# FloodRelay agent runtime image.
#
# NOT BUILT ON THIS MACHINE. Docker Desktop is installed but its Linux engine
# would not start here -- the `docker-desktop` WSL distro stays Stopped and
# every `docker` call hangs on the npipe -- so this has never been through a
# real build. What it is not is unreviewed: the AgentCore HTTP protocol contract
# was read against it and three things it would have failed on are fixed below,
# each marked CONTRACT. Expect to fix something on the first build anyway.
#
# ARM64 is REQUIRED by AgentCore Runtime, and this repository's machine is
# amd64, so a native build here would produce an image the runtime refuses.
# Build with buildx (which needs QEMU for the cross-build):
#
#   docker buildx build --platform linux/arm64 \
#     -f infra/agentcore/Dockerfile -t floodrelay-agent --load .
#
# To run it locally on an amd64 host, build without --platform. See the README
# in this directory for the full runbook and which steps have been executed.

# CONTRACT 1: AgentCore Runtime requires an ARM64 container. The ARG lets a
# local smoke test build natively while the deployed image is always arm64.
ARG TARGET_PLATFORM=linux/arm64
FROM --platform=${TARGET_PLATFORM} python:3.12-slim AS base

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
# `--extra ndma` installs pdfplumber. Without it the national situation report
# is unavailable in the one configuration that most needs it -- a deployment
# should not silently lose a data source to a missing optional extra.
RUN uv sync --frozen --no-install-project --no-dev --extra ndma

COPY backend/src ./src
COPY backend/seed ./seed
RUN uv sync --frozen --no-dev --extra ndma

# Run as a non-root user. The agent reads help requests from the public
# internet; it has no business running as root.
RUN useradd --create-home --uid 10001 floodrelay \
    && chown -R floodrelay:floodrelay /app
USER floodrelay

# CONTRACT 2: port 8080, bound to 0.0.0.0.
EXPOSE 8080

# In AWS the store is real DynamoDB and media goes to S3, so neither endpoint
# is set here. Everything else comes from the task definition.
ENV DDB_TABLE=floodrelay \
    S3_BUCKET=floodrelay-media \
    AWS_REGION=us-east-1 \
    MODEL_PROVIDER=bedrock \
    DEMO_MODE=false

# CONTRACT 3: AgentCore health-checks GET /ping, not /healthz, and expects
# {"status": "Healthy"}. This HEALTHCHECK is for `docker run` and any non-AWS
# orchestrator; it deliberately probes the same path AgentCore will, so a
# container that passes here passes there.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json,urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=4); sys.exit(0 if r.status==200 and json.load(r)['status'].startswith('Healthy') else 1)"

CMD ["uvicorn", "floodrelay.main:app", "--host", "0.0.0.0", "--port", "8080"]
