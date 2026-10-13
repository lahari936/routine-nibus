"""POST /api/homes/{home_id}/ask {"question": "..."} -> {"answer": "..."} for the dashboard's Ask box.
Runs at low effort to stay inside API Gateway's 30-second limit."""
import json

from agent.caregiver_agent import bedrock_model, build_agent
from lambdas.api.handler import allowed, email_of
from lambdas.common.http import as_json
from lambdas.common.store import DynamoStore

store = DynamoStore()


def handler(event, context):
    home_id = event["pathParameters"]["home_id"]
    if not allowed(store.home(home_id), email_of(event)):
        return as_json(404, {"error": "not found"})
    question = (json.loads(event.get("body") or "{}").get("question") or "").strip()[:500]
    if not question:
        return as_json(400, {"error": "question is required"})
    result = build_agent(store, home_id, model=bedrock_model(effort="low"))(question)
    return as_json(200, {"answer": str(result)})
