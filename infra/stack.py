import shutil
import subprocess
import sys
from pathlib import Path

import jsii
from aws_cdk import (ArnFormat, BundlingOptions, CfnOutput, DockerImage, Duration, ILocalBundling, Stack,
                     aws_apigatewayv2 as apigw, aws_apigatewayv2_authorizers as authorizers,
                     aws_apigatewayv2_integrations as integrations, aws_cognito as cognito, aws_dynamodb as ddb,
                     aws_iam as iam, aws_lambda as lambda_, aws_scheduler as scheduler,
                     aws_scheduler_targets as targets, aws_secretsmanager as secretsmanager,
                     aws_stepfunctions as sfn)
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = lambda_.Runtime.PYTHON_3_12


@jsii.implements(ILocalBundling)
class PipBundle:
    """Bundle core/ + lambdas/ + Linux wheels without Docker (pip --platform)."""

    def try_bundle(self, output_dir: str, *args, **kwargs) -> bool:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "lambdas/requirements.txt"),
                        "-t", output_dir, "--platform", "manylinux2014_x86_64", "--only-binary=:all:",
                        "--python-version", "3.12", "--implementation", "cp"], check=True)
        for pkg in ("core", "lambdas"):
            shutil.copytree(ROOT / pkg, Path(output_dir) / pkg, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "requirements.txt"))
        return True


class RoutineGuardianStack(Stack):
    def __init__(self, scope: Construct, id: str, *, ses_from: str, sms_enabled: bool, **kw) -> None:
        super().__init__(scope, id, **kw)

        table = ddb.TableV2(self, "Table", partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
                            time_to_live_attribute="ttl")
        secret = secretsmanager.Secret(
            self, "RingSecret",
            description='Ring app credentials as JSON: {"client_id": "...", "client_secret": "...", "hmac_key": "..."}')

        code = lambda_.Code.from_asset(str(ROOT), bundling=BundlingOptions(
            image=DockerImage.from_registry("public.ecr.aws/sam/build-python3.12"),
            local=PipBundle(),
            command=["bash", "-c", "pip install -r lambdas/requirements.txt -t /asset-output "
                                   "&& cp -r core lambdas /asset-output"]),
            exclude=[".venv", ".git", "cdk.out", "web", "node_modules", ".devdata", "**/__pycache__"])

        def fn(name: str, timeout: int = 30, memory: int = 256) -> lambda_.Function:
            f = lambda_.Function(self, name.title().replace("_", "") + "Fn", runtime=RUNTIME, code=code,
                                 handler=f"lambdas.{name}.handler.handler", timeout=Duration.seconds(timeout),
                                 memory_size=memory, environment={"TABLE_NAME": table.table_name,
                                                                  "RING_SECRET_ARN": secret.secret_arn})
            table.grant_read_write_data(f)
            secret.grant_read(f)
            return f

        webhook = fn("webhook_handler", timeout=10)  # Ring gives us 5 s; a cold start is the risk
        link, ack, api_fn = fn("link"), fn("ack"), fn("api")
        check = fn("deviation_check", timeout=120)
        nightly = fn("nightly_baseline", timeout=300, memory=512)
        backfill = fn("backfill", timeout=900, memory=512)
        escalation = fn("escalation")

        machine = sfn.StateMachine(
            self, "EscalationFlow",
            definition_body=sfn.DefinitionBody.from_file(str(ROOT / "stepfunctions/escalation.asl.json")),
            definition_substitutions={"EscalationFunctionArn": escalation.function_arn})
        escalation.grant_invoke(machine)
        machine.grant_start_execution(check)
        check.add_environment("STATE_MACHINE_ARN", machine.state_machine_arn)
        for f in (webhook, ack):
            machine.grant_task_response(f)
        # Not grant_task_response: the machine already references this function, that would be a cycle.
        escalation.add_to_role_policy(iam.PolicyStatement(
            actions=["states:SendTaskSuccess"],
            resources=[self.format_arn(service="states", resource="stateMachine", resource_name="*",
                                       arn_format=ArnFormat.COLON_RESOURCE_NAME)]))

        backfill.grant_invoke(link)
        link.add_environment("BACKFILL_FUNCTION", backfill.function_name)

        # --- HTTP API ---
        pool = cognito.UserPool(self, "Users", self_sign_up_enabled=True,
                                sign_in_aliases=cognito.SignInAliases(email=True),
                                auto_verify=cognito.AutoVerifiedAttrs(email=True))
        web_client = pool.add_client("Web", auth_flows=cognito.AuthFlow(user_srp=True), generate_secret=False)
        api = apigw.HttpApi(self, "Api", cors_preflight=apigw.CorsPreflightOptions(
            allow_origins=["*"], allow_headers=["authorization", "content-type"],
            allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.PUT]))
        jwt = authorizers.HttpUserPoolAuthorizer("Cognito", pool, user_pool_clients=[web_client])

        def route(path: str, methods: list[apigw.HttpMethod], f: lambda_.Function, auth=None) -> None:
            api.add_routes(path=path, methods=methods, authorizer=auth,
                           integration=integrations.HttpLambdaIntegration(f"{f.node.id}{len(api.node.children)}", f))

        GET, POST, PUT = apigw.HttpMethod.GET, apigw.HttpMethod.POST, apigw.HttpMethod.PUT
        route("/ring/webhook", [POST], webhook)
        route("/ring/token-exchange", [POST], link)
        route("/ring/link", [GET, POST], link)
        route("/ack", [GET, POST], ack)
        route("/api/homes", [GET], api_fn, jwt)
        route("/api/homes/{home_id}", [GET], api_fn, jwt)
        route("/api/homes/{home_id}/events", [GET], api_fn, jwt)
        route("/api/homes/{home_id}/alerts", [GET], api_fn, jwt)
        route("/api/homes/{home_id}/settings", [PUT], api_fn, jwt)

        escalation.add_environment("ACK_BASE_URL", api.api_endpoint)
        escalation.add_environment("SES_FROM", ses_from)
        escalation.add_environment("SNS_SMS_ENABLED", str(sms_enabled).lower())
        escalation.add_to_role_policy(iam.PolicyStatement(actions=["ses:SendEmail", "sns:Publish"], resources=["*"]))

        # --- schedules ---
        scheduler.Schedule(self, "DeviationCheckEvery15Min", schedule=scheduler.ScheduleExpression.rate(Duration.minutes(15)),
                           target=targets.LambdaInvoke(check))
        scheduler.Schedule(self, "NightlyBaseline", schedule=scheduler.ScheduleExpression.cron(hour="8", minute="0"),
                           target=targets.LambdaInvoke(nightly))

        for name, value in {"ApiUrl": api.api_endpoint, "TableName": table.table_name, "RingSecretArn": secret.secret_arn,
                            "UserPoolId": pool.user_pool_id, "UserPoolClientId": web_client.user_pool_client_id,
                            "StateMachineArn": machine.state_machine_arn, "DeviationCheckFunction": check.function_name,
                            "NightlyBaselineFunction": nightly.function_name}.items():
            CfnOutput(self, name, value=value)
