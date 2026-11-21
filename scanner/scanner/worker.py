"""SQS loop: scan each new upload and move it to the clean or quarantine bucket.

Flow per message (S3 event notification from the uploads bucket):
  1. check size (over MAX_BYTES -> quarantine without scanning)
  2. stream the object from S3 straight into clamd (nothing is written to disk)
  3. copy to the clean or quarantine bucket, then delete from uploads
  4. only then delete the SQS message
If anything fails the message is left in the queue: SQS redelivers it after
the visibility timeout and moves it to the dead-letter queue after a few tries.
An upload is NEVER moved to the clean bucket without a clean verdict.
"""
import json
import os
import time
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from . import clamd_client, config, obs

_s3 = None
_sqs = None


def clients():
    global _s3, _sqs
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=config.AWS_REGION)
        _sqs = boto3.client("sqs", region_name=config.AWS_REGION)
    return _s3, _sqs


def reset_clients():
    global _s3, _sqs
    _s3 = _sqs = None


def heartbeat():
    try:
        with open(config.HEARTBEAT_FILE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass  # the health check will notice a stale file


def extract_objects(body: str) -> list:
    """Return [(bucket, key)] from an S3 notification; [] for test events and
    anything we do not recognise (logged, then dropped)."""
    try:
        data = json.loads(body)
    except ValueError:
        obs.log("bad_message", reason="not_json")
        return []
    if not isinstance(data, dict):
        return []
    if data.get("Event") == "s3:TestEvent":
        return []
    out = []
    for rec in data.get("Records", []):
        try:
            if not rec["eventName"].startswith("ObjectCreated"):
                continue
            bucket = rec["s3"]["bucket"]["name"]
            key = unquote_plus(rec["s3"]["object"]["key"])
        except (KeyError, TypeError, AttributeError):
            obs.log("bad_message", reason="unexpected_shape")
            continue
        if bucket != config.UPLOADS_BUCKET:
            obs.log("bad_message", reason="unexpected_bucket")
            continue
        out.append((bucket, key))
    return out


def _chunks(body):
    while True:
        data = body.read(clamd_client.CHUNK)
        if not data:
            return
        yield data


def _move(s3, key: str, dest_bucket: str):
    s3.copy_object(
        Bucket=dest_bucket,
        Key=key,
        CopySource={"Bucket": config.UPLOADS_BUCKET, "Key": key},
    )
    s3.delete_object(Bucket=config.UPLOADS_BUCKET, Key=key)


def process_object(key: str) -> str:
    """Scan one upload and move it. Returns the outcome label.
    Raises on any failure so the message stays in the queue."""
    s3, _ = clients()
    started = time.time()
    try:
        head = s3.head_object(Bucket=config.UPLOADS_BUCKET, Key=key)
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            obs.log("already_handled", key=key)
            return "already_handled"
        raise

    if head["ContentLength"] > config.MAX_BYTES:
        verdict = clamd_client.Verdict("too_large")
    else:
        obj = s3.get_object(Bucket=config.UPLOADS_BUCKET, Key=key)
        try:
            verdict = clamd_client.scan_stream(_chunks(obj["Body"]))
        finally:
            obj["Body"].close()

    if verdict.status == "clean":
        _move(s3, key, config.CLEAN_BUCKET)
        obs.metric("ScansClean")
    else:
        _move(s3, key, config.QUARANTINE_BUCKET)
        if verdict.status == "infected":
            obs.metric("ScansInfected", signature=verdict.signature)
        else:
            obs.metric("ScansTooLarge")
    ms = (time.time() - started) * 1000
    obs.metric("ScanDurationMs", ms, unit="Milliseconds")
    obs.log("scan_done", key=key, verdict=verdict.status, signature=verdict.signature, ms=round(ms))
    return verdict.status


def handle_message(msg: dict) -> bool:
    """Process one SQS message. True = delete it, False = leave for retry."""
    objects = extract_objects(msg.get("Body", ""))
    try:
        for _bucket, key in objects:
            process_object(key)
        return True
    except Exception as err:  # noqa: BLE001 - every failure means "retry later"
        obs.metric("ScansFailed")
        obs.log("scan_failed", error=type(err).__name__, detail=str(err)[:200])
        return False


def run_once() -> int:
    """Poll once; returns the number of messages received."""
    _, sqs = clients()
    resp = sqs.receive_message(
        QueueUrl=config.QUEUE_URL,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=config.POLL_WAIT_SECONDS,
    )
    msgs = resp.get("Messages", [])
    for m in msgs:
        if handle_message(m):
            sqs.delete_message(QueueUrl=config.QUEUE_URL, ReceiptHandle=m["ReceiptHandle"])
    heartbeat()
    return len(msgs)


def run_forever():
    missing = config.missing_settings()
    if missing:
        obs.log("config_missing", names=missing)
        raise SystemExit(2)
    obs.log("worker_started", uploads=config.UPLOADS_BUCKET, max_bytes=config.MAX_BYTES)
    while True:
        try:
            run_once()
        except (ClientError, BotoCoreError) as err:
            # SQS itself failed (throttling, permissions, endpoint unreachable, no credentials).
            # Back off and keep going instead of exiting.
            code = err.response["Error"]["Code"] if isinstance(err, ClientError) else type(err).__name__
            obs.log("queue_error", error=code)
            time.sleep(config.QUEUE_ERROR_BACKOFF)
            heartbeat()
