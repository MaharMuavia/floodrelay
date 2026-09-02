# CDK stack

**Not deployed, never synthesised.** No AWS credentials were available on the
build machine. Written from the access patterns in `backend/src/floodrelay/store/`
rather than from a template, and unverified — run `cdk synth` first.

```bash
pip install -r requirements.txt
cdk synth
cdk deploy
```

## What it creates

| Resource | Notes |
|---|---|
| DynamoDB `floodrelay` | Single table, GSI1 for the board query, PAY_PER_REQUEST, **RETAIN** on delete |
| S3 `floodrelay-media-<account>` | Encrypted, no public access, 90-day expiry on uploads |
| `FloodRelayAgentExecutionRole` | Scoped to the two Nova models, the table, the bucket, and its log group |
| Lambda + hourly EventBridge rule | Calls `POST /internal/rescan` so recency decay keeps the board honest |

Both stores are `RemovalPolicy.RETAIN`. The audit trail is append-only and the
request history records what was decided about real people; a stack teardown
should not take it with them. Deleting the stack leaves them behind on purpose.

`AGENT_URL` on the Lambda is a placeholder — fill it in once the runtime exists.
Set `INTERNAL_TOKEN` on both the Lambda and the runtime, or the rescan endpoint
is unauthenticated.
