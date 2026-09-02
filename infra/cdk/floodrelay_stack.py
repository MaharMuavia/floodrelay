"""CDK stack for FloodRelay's AWS resources.

**NOT DEPLOYED.** No AWS credentials were available on the build machine, so this
has never been synthesised or deployed. It is written from the access patterns
the code actually uses (see `store/table.py`) rather than from a template, and
it is unverified. `cdk synth` is the first thing to run against it.

Creates the DynamoDB table with GSI1, the media bucket, an execution role scoped
to what the agent genuinely needs, and the hourly rescan schedule.

    pip install aws-cdk-lib constructs
    cdk synth
    cdk deploy
"""

from __future__ import annotations

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct


class FloodRelayStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- store ----------------------------------------------------------
        #
        # Single table. PAY_PER_REQUEST because a relief console's traffic is
        # bursty by definition: nothing for weeks, then a district floods.
        table = dynamodb.Table(
            self,
            "FloodRelayTable",
            table_name="floodrelay",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            # The audit trail is append-only and the request history is the
            # record of what was decided about real people. Do not let a stack
            # teardown take it with them.
            removal_policy=RemovalPolicy.RETAIN,
        )

        # The board query: most urgent first within a status. gsi1sk stores
        # inverted urgency so a forward scan is already in the right order.
        table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="gsi1pk", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="gsi1sk", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # --- media ----------------------------------------------------------
        media = s3.Bucket(
            self,
            "FloodRelayMedia",
            bucket_name=f"floodrelay-media-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                # Photos are only useful while the response is active.
                s3.LifecycleRule(
                    id="expire-uploads",
                    expiration=Duration.days(90),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        # --- execution role -------------------------------------------------
        role = iam.Role(
            self,
            "AgentExecutionRole",
            role_name="FloodRelayAgentExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Runtime role for the FloodRelay agent.",
        )

        table.grant_read_write_data(role)
        media.grant_read_write(role)

        # Only the two Nova models. A wildcard here would let a
        # misconfiguration silently invoke something the project never chose.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/amazon.nova-pro-v1:0",
                    f"arn:aws:bedrock:{self.region}::foundation-model/amazon.nova-lite-v1:0",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.amazon.nova-pro-v1:0",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.amazon.nova-lite-v1:0",
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup",
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/agentcore/*"],
            )
        )

        # --- periodic rescan -------------------------------------------------
        #
        # Recency decays to zero over six hours, so hourly keeps the board
        # honest without re-scoring constantly. This calls POST /internal/rescan
        # -- NOT /demo/rescan, which refuses when DEMO_MODE is false.
        rescan = lambda_.Function(
            self,
            "RescanTrigger",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.seconds(30),
            environment={
                "AGENT_URL": "<AGENT_RUNTIME_URL>",
            },
            code=lambda_.Code.from_inline(
                "import json, os, urllib.request\n"
                "\n"
                "def handler(event, context):\n"
                "    url = os.environ['AGENT_URL'].rstrip('/') + '/internal/rescan'\n"
                "    req = urllib.request.Request(url, data=b'{}', method='POST')\n"
                "    req.add_header('Content-Type', 'application/json')\n"
                "    token = os.environ.get('INTERNAL_TOKEN')\n"
                "    if token:\n"
                "        req.add_header('X-Internal-Token', token)\n"
                "    with urllib.request.urlopen(req, timeout=25) as resp:\n"
                "        return {'status': resp.status, 'body': json.loads(resp.read())}\n"
            ),
        )

        events.Rule(
            self,
            "RescanSchedule",
            schedule=events.Schedule.rate(Duration.hours(1)),
            targets=[targets.LambdaFunction(rescan)],
            description="Re-score open FloodRelay requests so recency decay applies.",
        )
