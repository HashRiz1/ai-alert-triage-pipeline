import azure.functions as func
import logging
import json
import uuid
from datetime import datetime, timezone, timedelta

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.cosmos import CosmosClient
import requests

app = func.FunctionApp()


KEY_VAULT_URL = "https://key-sentinel-triage.vault.azure.net/"
WORKSPACE_ID = "80b0bbd4-f8fb-4fb1-a3fb-430951198354"
COSMOS_ENDPOINT = "https://cosmos-sentinel-triage.documents.azure.com:443/"
COSMOS_DATABASE = "TriageDB"
COSMOS_CONTAINER = "Incidents"
OPENAI_DEPLOYMENT = "gpt-5-mini" 
OPENAI_API_VERSION = "2025-04-01-preview"

SYSTEM_PROMPT = """You are a security operations triage assistant. You will be given a security incident's raw details (title, description, severity, entities). Analyze it and respond with ONLY valid JSON in this exact schema, no markdown formatting, no code blocks, no explanatory text before or after:

{
  "severity": "low | medium | high | critical",
  "mitre_technique": "T#### or 'unknown'",
  "technique_name": "string",
  "summary": "2-3 sentence plain-language explanation",
  "recommended_action": "string",
  "confidence": "low | medium | high"
}

Base your severity assessment on the incident's actual risk indicators, not just the input severity field. If MITRE technique cannot be determined from the given information, use 'unknown' for both mitre_technique and technique_name."""


@app.timer_trigger(schedule="0 */15 * * * *", arg_name="myTimer", run_on_startup=False, use_monitor=False)
def TriageOrchestrator(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info('The timer is past due!')

    logging.info('TriageOrchestrator started.')

    credential = DefaultAzureCredential()

    try:
        kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
        openai_key = kv_client.get_secret("openai-api-key").value
        openai_endpoint = kv_client.get_secret("openai-endpoint").value
    except Exception as e:
        logging.error(f"Failed to retrieve secrets from Key Vault: {e}")
        return

 
    try:
        cosmos_client = CosmosClient(url=COSMOS_ENDPOINT, credential=credential)
        database = cosmos_client.get_database_client(COSMOS_DATABASE)
        container = database.get_container_client(COSMOS_CONTAINER)
    except Exception as e:
        logging.error(f"Failed to connect to Cosmos DB: {e}")
        return

 
    try:
        existing_ids = set()
        for item in container.query_items(
            query="SELECT c.incident_id FROM c",
            enable_cross_partition_query=True
        ):
            existing_ids.add(item["incident_id"])
        logging.info(f"Found {len(existing_ids)} already-processed incidents.")
    except Exception as e:
        logging.error(f"Failed to query existing incidents from Cosmos DB: {e}")
        existing_ids = set()


    try:
        logs_client = LogsQueryClient(credential)
        kql_query = """
        SecurityIncident
        | take 20
        | project IncidentNumber, Title, Description, Severity, Status, TimeGenerated, IncidentUrl
        """
        response = logs_client.query_workspace(
            workspace_id=WORKSPACE_ID,
            query=kql_query,
            timespan=timedelta(days=7)
        )

        if response.status != LogsQueryStatus.SUCCESS:
            logging.error(f"Sentinel query failed or partial: {response}")
            return

        table = response.tables[0]
        columns = table.columns
        rows = table.rows

    except Exception as e:
        logging.error(f"Failed to query Sentinel: {e}")
        return

    logging.info(f"Retrieved {len(rows)} incidents from Sentinel.")


    processed_count = 0
    for row in rows:
        row_dict = dict(zip(columns, row))
        incident_id = str(row_dict.get("IncidentNumber", uuid.uuid4()))

        if incident_id in existing_ids:
            continue  # already processed, skip

        raw_alert = {
            "incident_number": incident_id,
            "title": row_dict.get("Title"),
            "description": row_dict.get("Description"),
            "severity": row_dict.get("Severity"),
            "status": row_dict.get("Status"),
            "time_generated": str(row_dict.get("TimeGenerated")),
        }

        enrichment, decision_log = call_openai_and_parse(
            raw_alert, openai_endpoint, openai_key
        )

        record = {
            "id": incident_id,  # Cosmos requires id field
            "incident_id": incident_id,
            "raw_alert": raw_alert,
            "enrichment": enrichment,
            "decision_log": decision_log,
            "human_review": {
                "status": "pending",
                "reviewed_by": None,
                "reviewed_at": None
            }
        }

        try:
            container.upsert_item(record)
            processed_count += 1
        except Exception as e:
            logging.error(f"Failed to write record {incident_id} to Cosmos DB: {e}")

    logging.info(f"TriageOrchestrator finished. Processed {processed_count} new incidents.")


def call_openai_and_parse(raw_alert: dict, endpoint: str, api_key: str) -> tuple[dict, dict]:
    """Calls Azure OpenAI, parses the JSON response. Returns (enrichment, decision_log)."""
    queried_at = datetime.now(timezone.utc).isoformat()

    user_message = (
        f"Title: {raw_alert.get('title')}\n"
        f"Description: {raw_alert.get('description')}\n"
        f"Severity: {raw_alert.get('severity')}\n"
    )

    url = f"{endpoint}openai/deployments/{OPENAI_DEPLOYMENT}/chat/completions?api-version={OPENAI_API_VERSION}"
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key
    }
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
    }

    raw_model_output = ""
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        raw_model_output = result["choices"][0]["message"]["content"]

        # Strip potential markdown fencing just in case
        cleaned = raw_model_output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json\n", "", 1)

        enrichment = json.loads(cleaned)

        decision_log = {
            "queried_at": queried_at,
            "model": OPENAI_DEPLOYMENT,
            "raw_model_output": raw_model_output,
            "parse_status": "success"
        }
        return enrichment, decision_log

    except Exception as e:
        logging.error(f"OpenAI call or parse failed: {e}")
        enrichment = {
            "severity": "unknown",
            "mitre_technique": "unknown",
            "technique_name": "unknown",
            "summary": "enrichment_failed",
            "recommended_action": "Manual review required — automated enrichment failed.",
            "confidence": "unknown"
        }
        decision_log = {
            "queried_at": queried_at,
            "model": OPENAI_DEPLOYMENT,
            "raw_model_output": raw_model_output,
            "parse_status": "failed"
        }
        return enrichment, decision_log