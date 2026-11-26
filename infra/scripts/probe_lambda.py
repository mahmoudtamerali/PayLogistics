"""Throwaway probe for T56 (isolation) and the authenticated records-service path test. Deploy it as a
Lambda in the PRIVATE subnets, with a security group the ALB allows. Delete the function after the test.

Old mode (unchanged): {"url": "http://<alb-dns>/health"} -> one GET, no headers, no auth (health needs
no token). Returns {"status": <int>, "body": <first 200 chars>} or {"status": 0, "error": <type>}.

New mode (any of these optional fields present, added to exercise the authenticated KMS/DynamoDB path):
  url             - required, same as above
  method          - "GET" (default) or "POST"
  json_body       - dict, sent as the JSON request body
  extra_headers   - dict of extra request headers
  token_secret_id - Secrets Manager secret id or ARN; if set, its SecretString is read and sent as the
                    X-Internal-Token header. The token is never returned, logged or printed by this
                    function - only used in-memory to build the outgoing request header.
  token_stage     - "AWSCURRENT" (default) or "AWSPREVIOUS"
  caller_id       - sent as X-Caller-Id (only meaningful together with token_secret_id)
  caller_role     - sent as X-Caller-Role (only meaningful together with token_secret_id)

Returns {"status": <int>, "body": <first 300 chars of the service response>} or
{"status": 0, "error": <type>}. The body is the service's own response; the secret value is never part
of the return value, a log line or a print.
"""
import json
import urllib.error
import urllib.request

import boto3

_NEW_MODE_KEYS = ("method", "json_body", "extra_headers", "token_secret_id", "caller_id", "caller_role")


def _token(secret_id: str, stage: str) -> str:
    client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=secret_id, VersionStage=stage)["SecretString"]


def handler(event, context=None):
    if not any(k in event for k in _NEW_MODE_KEYS):
        # Unchanged old behaviour: one plain GET, no headers, 200-char body.
        try:
            with urllib.request.urlopen(event["url"], timeout=5) as r:  # noqa: S310
                return {"status": r.status, "body": r.read(200).decode("utf-8", "replace")}
        except urllib.error.HTTPError as e:
            return {"status": e.code}
        except Exception as e:  # noqa: BLE001
            return {"status": 0, "error": type(e).__name__}

    try:
        headers = dict(event.get("extra_headers") or {})
        token_secret_id = event.get("token_secret_id")
        if token_secret_id:
            headers["X-Internal-Token"] = _token(token_secret_id, event.get("token_stage", "AWSCURRENT"))
            if event.get("caller_id"):
                headers["X-Caller-Id"] = event["caller_id"]
            if event.get("caller_role"):
                headers["X-Caller-Role"] = event["caller_role"]

        data = None
        json_body = event.get("json_body")
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(
            event["url"], data=data, headers=headers, method=event.get("method", "GET").upper()
        )
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
            return {"status": r.status, "body": r.read(300).decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "error": type(e).__name__}
