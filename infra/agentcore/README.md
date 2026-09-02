# AgentCore Runtime deployment

**Status: written, never executed.** The Docker daemon was not running on the
build machine and there were no AWS credentials, so the image has not been
built, pushed, or launched. Nothing in this directory has been verified beyond
the YAML parsing and a read-through.

It is included because it is most of the work and it records the decisions, not
because it is proven. Budget time to fix something on the first build.

## What still has to be true before this works

1. **Bedrock model access.** Nova Pro and Nova Lite must be enabled in
   `us-east-1` for the account. Without it `get_model()` raises
   `ModelUnavailable` at the first request rather than at startup.
2. **The table and bucket exist.** `floodrelay` (with GSI1 on `gsi1pk`/`gsi1sk`)
   and `floodrelay-media`. `DynamoBackend.ensure_table()` creates the table when
   it is missing, but the execution role needs `dynamodb:CreateTable` for that
   to work, which is usually not what you want in production — create it with
   the CDK stack or by hand instead.
3. **An execution role** with the permissions below.

## Execution role permissions

The minimum this actually uses:

| Service | Actions |
|---|---|
| Bedrock | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` on the two Nova model ARNs |
| DynamoDB | `GetItem`, `PutItem`, `DeleteItem`, `Query`, `Scan` on the table and `index/GSI1` |
| S3 | `GetObject`, `PutObject` on `arn:aws:s3:::floodrelay-media/*` |
| CloudWatch | `logs:CreateLogStream`, `logs:PutLogEvents` |

Note `Scan`: the board and audit queries scan by key prefix rather than using a
secondary index for every access pattern. That is a deliberate trade for a
district-scale workload (see `docs/decisions.md`), and it is worth revisiting
before any larger deployment.

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

Put a real contact address in `NOMINATIM_USER_AGENT`. Nominatim returns
HTTP 403 for placeholder domains such as `example.org`; this was verified
against the live service.

## Deploying

```bash
aws ecr create-repository --repository-name floodrelay-agent
docker build -f infra/agentcore/Dockerfile -t floodrelay-agent .
docker tag floodrelay-agent:latest "$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/floodrelay-agent:latest"
docker push "$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/floodrelay-agent:latest"
```

Then create the runtime from `runtime-config.yaml`, adjusting the account id and
role ARN. Confirm `/healthz` reports `store: dynamodb(aws)` and the expected
model ids — it is the quickest way to catch configuration that silently fell
back to a local default.

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
