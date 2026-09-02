#!/usr/bin/env python3
"""CDK entry point. NOT DEPLOYED -- see floodrelay_stack.py."""

import aws_cdk as cdk

from floodrelay_stack import FloodRelayStack

app = cdk.App()
FloodRelayStack(
    app,
    "FloodRelayStack",
    env=cdk.Environment(region="us-east-1"),
    description="FloodRelay: table, media bucket, agent role and rescan schedule.",
)
app.synth()
