import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aws_cdk as cdk  # noqa: E402

from stack import RoutineGuardianStack  # noqa: E402

app = cdk.App()
RoutineGuardianStack(
    app, "RoutineGuardian",
    ses_from=app.node.try_get_context("ses_from") or "alerts@example.com",
    sms_enabled=(app.node.try_get_context("sms_enabled") or "true") == "true",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")),
)
app.synth()
