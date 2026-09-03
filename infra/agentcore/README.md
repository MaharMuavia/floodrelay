# AgentCore Runtime deployment

**Status: the application meets the runtime contract and was run against it. The
container was never built, and nothing was deployed.**

Every step below is marked:

- ✅ **executed** — run on this machine, output in this file or the root README.
- ⚠️ **verified another way** — the step itself did not run, but what it depends
  on was checked directly.
- ❌ **not executed** — written from the documentation, never run. Assume it
  needs fixing.

What blocked the container steps: Docker Desktop is installed (CLI 29.5.3) but
its Linux engine would not start here. The `docker-desktop` WSL distro stays
`Stopped` and every `docker` call hangs on
`npipe:////./pipe/dockerDesktopLinuxEngine`. There are also no AWS credentials —
`aws sts get-caller-identity` returns `NoCredentials`.

---

## What was actually checked, and what it found

Reading the [AgentCore HTTP protocol contract][contract] against this repository
turned up **three things the original image would have failed on.** All three
are now fixed in code and pinned by `backend/tests/test_agentcore_contract.py`
(13 tests, offline).

| # | The contract requires | What we had | Now |
|---|---|---|---|
| 1 | `POST /invocations` | nothing — only `/intake` | `api/routes_agentcore.py` |
| 2 | `GET /ping` → `{"status":"Healthy"}` | only `/healthz` | same file |
| 3 | An **ARM64** image | no platform pinned; this host is amd64 | `--platform` in the Dockerfile, `buildx` in the runbook |

Any one of them would have produced a container AgentCore marks unhealthy and
restarts, with the reason visible only in CloudWatch.

Two smaller notes from the same contract, both handled:

- `/ping` may answer `HealthyBusy`, and the runtime **keeps a session alive**
  while it sees that. So it is reported only while pipeline runs are genuinely
  queued or executing, never as a default.
- `time_of_last_update` is deliberately **omitted**. The contract warns that a
  timestamp advancing on every ping reads as a continuous status change, stops
  the idle timeout from ever firing, and exhausts the session quota.

### ✅ The app served the contract under the deployment environment

Not in a container — directly, with the same environment the runtime sets:

```
MODEL_PROVIDER=bedrock AWS_REGION=us-east-1 DDB_TABLE=floodrelay \
DDB_ENDPOINT=memory DEMO_MODE=false S3_BUCKET=floodrelay-media \
uv run uvicorn floodrelay.main:app --host 0.0.0.0 --port 8080
```

```
$ curl -i http://127.0.0.1:8080/ping
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"status":"Healthy"}
```

```
$ curl http://127.0.0.1:8080/healthz
{
  "status": "ok",
  "store": "memory",
  "models": {
    "provider": "bedrock",
    "heavy": "us.amazon.nova-pro-v1:0",
    "light": "us.amazon.nova-lite-v1:0",
    "tool_calling": "active",
    "tool_calling_detail": "the model chooses and calls the @tool functions itself"
  },
  "demo_mode": false,
  "checks": {
    "store": "ok",
    "tracing": "disabled (OTEL_EXPORTER_OTLP_ENDPOINT is not set)",
    "tool_calling": "active: the model chooses and calls tools"
  }
}
```

### ✅ An invocation with no credentials fails the way it is supposed to

This is the useful negative result. With `MODEL_PROVIDER=bedrock` and no AWS
session, the model call fails — and the failure still leaves a coordinator
something to answer rather than a request stuck mid-pipeline:

```
$ curl -X POST http://127.0.0.1:8080/invocations -H 'Content-Type: application/json' \
    -d '{"prompt":"4 log chhat par phanse hain Pir Sabaq, pani tez barh raha hai"}'
HTTP 500
{"detail": "the run failed and was raised as a decision card for a human:
            NoCredentialsError: Unable to locate credentials"}

$ curl http://127.0.0.1:8080/decisions
{"decisions": [{
  "id": "d_a52bc96f",
  "kind": "processing_failed",
  "heading": "Couldn't read this message automatically.",
  "reasoning": "The agent stopped partway through: NoCredentialsError: Unable to
                locate credentials. The message itself is intact and shown in
                full on the request, so nothing has been lost.",
  "options": [{"id": "RETRY", "is_dispatch": false},
              {"id": "MANUAL", "is_dispatch": false}]
}]}
```

Note both options are `is_dispatch: false`. A failure cannot authorise anything.

### ✅ OTel activates on `OTEL_EXPORTER_OTLP_ENDPOINT`

```
$ # unset
"tracing": "disabled (OTEL_EXPORTER_OTLP_ENDPOINT is not set)"

$ OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 uv run uvicorn ...
"tracing": "exporting to http://localhost:4317"
```

`telemetry.py` builds a `TracerProvider` with a `BatchSpanProcessor` over the
OTLP/gRPC exporter, and never fails the startup if the collector is down — a
console that refuses to boot because a tracing sidecar is missing would be a poor
trade. Point it at the ADOT collector sidecar in AgentCore, or at Jaeger locally.

---

## The runbook

### 1. ❌ Build the image (ARM64)

AgentCore requires ARM64. On an amd64 machine this is a cross-build and needs
QEMU:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx create --name floodrelay --use 2>/dev/null || docker buildx use floodrelay
docker buildx build --platform linux/arm64 \
  -f infra/agentcore/Dockerfile \
  -t floodrelay-agent:latest --load .
```

On an ARM host (Apple silicon, Graviton) drop `buildx` and the platform flag.

**Not executed** — no Docker engine. The most likely thing to break first is the
`uv sync --frozen` layers: the lockfile resolves on this machine's platform, and
a frozen sync on `linux/arm64` will fail if any dependency lacks an arm64 wheel
and needs a compiler the slim image does not have. If that happens, add
`build-essential` to a builder stage rather than dropping `--frozen`.

### 2. ⚠️ Smoke-test the container

```bash
docker run --rm -p 8080:8080 \
  -e MODEL_PROVIDER=bedrock -e AWS_REGION=us-east-1 \
  -e DDB_ENDPOINT=http://host.docker.internal:8000 \
  -e AWS_ACCESS_KEY_ID=local -e AWS_SECRET_ACCESS_KEY=local \
  floodrelay-agent:latest

curl http://localhost:8080/ping        # expect {"status":"Healthy"}
curl http://localhost:8080/healthz     # expect store: dynamodb(local)
curl -X POST http://localhost:8080/invocations \
  -H 'Content-Type: application/json' -d '{"prompt":"chhat par phanse hain"}'
```

DynamoDB Local first, via `infra/docker-compose.yml`.

**Not executed as a container.** What *was* executed is the same three requests
against the same application under the same environment, outside Docker — see
above. What remains unverified is therefore the image, not the app.

### 3. ❌ Push to ECR

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1

aws ecr create-repository --repository-name floodrelay-agent --region "$REGION"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

docker tag floodrelay-agent:latest "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/floodrelay-agent:latest"
docker push "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/floodrelay-agent:latest"
```

**Not executed** — no AWS credentials (`aws sts get-caller-identity` →
`NoCredentials`).

### 4. ❌ Create the runtime

```bash
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name floodrelay_agent \
  --agent-runtime-artifact "containerConfiguration={containerUri=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/floodrelay-agent:latest}" \
  --network-configuration "networkMode=PUBLIC" \
  --role-arn "arn:aws:iam::$ACCOUNT:role/FloodRelayAgentExecutionRole" \
  --region "$REGION"
```

`runtime-config.yaml` in this directory holds the same settings in declarative
form, including the environment and the hourly rescan schedule. **Verify the
field names against the current API before trusting either** — the API shape has
moved, and neither has been applied.

### 5. ❌ Invoke it

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn "arn:aws:bedrock-agentcore:$REGION:$ACCOUNT:runtime/floodrelay_agent" \
  --payload '{"prompt":"4 log chhat par phanse hain Pir Sabaq"}' \
  --region "$REGION" /dev/stdout
```

**Not executed.**

---

## Execution role policy

The minimum this actually uses. `<ACCOUNT>` and `<REGION>` are placeholders.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeTheTwoNovaModels",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:<REGION>::foundation-model/amazon.nova-pro-v1:0",
        "arn:aws:bedrock:<REGION>::foundation-model/amazon.nova-lite-v1:0",
        "arn:aws:bedrock:<REGION>:<ACCOUNT>:inference-profile/us.amazon.nova-pro-v1:0",
        "arn:aws:bedrock:<REGION>:<ACCOUNT>:inference-profile/us.amazon.nova-lite-v1:0"
      ]
    },
    {
      "Sid": "SingleTableAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:<REGION>:<ACCOUNT>:table/floodrelay",
        "arn:aws:dynamodb:<REGION>:<ACCOUNT>:table/floodrelay/index/GSI1"
      ]
    },
    {
      "Sid": "MediaBucket",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::floodrelay-media/*"
    },
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT>:log-group:/aws/bedrock-agentcore/*"
    },
    {
      "Sid": "PullTheImage",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "*"
    },
    {
      "Sid": "TheWebhookSecret",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:<REGION>:<ACCOUNT>:secret:floodrelay/internal-token-*",
        "arn:aws:secretsmanager:<REGION>:<ACCOUNT>:secret:floodrelay/webhook-secret-*"
      ]
    }
  ]
}
```

Trust policy for the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {"StringEquals": {"aws:SourceAccount": "<ACCOUNT>"}}
  }]
}
```

Two notes on this policy:

- **`dynamodb:CreateTable` is deliberately absent.** `DynamoBackend.ensure_table()`
  will create the table when it is missing, which is convenient locally and wrong
  in production. Create it with the CDK stack in `infra/cdk/` or by hand.
- **`Scan` is there because the board and audit queries scan by key prefix**
  rather than having a secondary index for every access pattern. That is a
  deliberate trade for a district-scale workload (`docs/decisions.md` §4), and it
  is worth revisiting before any larger deployment.

---

## What still has to be true

1. **Bedrock model access.** Nova Pro and Nova Lite enabled in the region for the
   account. Without it `get_model()` raises `ModelUnavailable` at the first
   request rather than at startup — and, as the invocation above shows, that
   surfaces as a `processing_failed` card rather than a silent stall.
2. **The table and bucket exist.** `floodrelay` with GSI1 on `gsi1pk`/`gsi1sk`,
   and `floodrelay-media`.
3. **An execution role** with the policy above.

## Environment

Set on the runtime, not baked into the image:

```
MODEL_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL_HEAVY=us.amazon.nova-pro-v1:0
BEDROCK_MODEL_LIGHT=us.amazon.nova-lite-v1:0
DDB_TABLE=floodrelay
S3_BUCKET=floodrelay-media
NOMINATIM_USER_AGENT=FloodRelay/0.1 (your-real-contact@example)
GEOCODE_VIEWBOX=71.65,34.15,72.30,33.85
OTEL_EXPORTER_OTLP_ENDPOINT=<collector endpoint>
DEMO_MODE=false
CORS_ORIGINS=<the console's origin>
```

`DDB_ENDPOINT` and `S3_ENDPOINT` must be **unset** in AWS — they exist to point
at DynamoDB Local and MinIO.

Put a real contact address in `NOMINATIM_USER_AGENT`. Nominatim returns HTTP 403
for placeholder domains such as `example.org`; verified against the live service.

`INTERNAL_TOKEN` and `WEBHOOK_SECRET` are secrets, not environment literals.
`WEBHOOK_SECRET` in particular **fails closed**: with no value, `/intake/webhook`
refuses every request rather than accepting unsigned ones.

## The periodic rescan

The scheduler targets `POST /internal/rescan`, which re-scores every open request
so recency decay actually moves the board.

Writing this file is what surfaced the bug it used to describe: rescan was
originally only reachable at `POST /demo/rescan`, behind `DEMO_MODE`. In a
production configuration (`DEMO_MODE=false`) the scheduled job would have been
refused every time — silently dead in exactly the setup that needs it. It now
lives on its own router, outside the demo gate, and `/demo/rescan` is kept only
as a convenience alias for the demo UI.

Set `INTERNAL_TOKEN` and pass it as `X-Internal-Token`. When the variable is
unset the route is unauthenticated, which is fine locally and is not fine on a
public runtime.

[contract]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html
