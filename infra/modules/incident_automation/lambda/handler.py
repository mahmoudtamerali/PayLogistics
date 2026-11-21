"""Turns a CloudWatch alarm state change into one clear incident message.

Triggered by EventBridge ("CloudWatch Alarm State Change", state ALARM).
It only READS the event and PUBLISHES to SNS. It changes nothing in the account, on purpose:
automatic fixes on a clinic system should be a human decision (see README, "Not automated").

Environment:
  TOPIC_ARN   SNS topic that receives the incident message (required)
  ENV_NAME    label shown in the subject, default "securecare"
"""
import json
import os

import boto3

# alarm-name suffix -> (severity, what it means, first thing to check)
# Suffixes match the alarms created by modules/monitoring and modules/waf_alarm.
# PLACEHOLDER texts: the team should edit them to match the real runbook at T74/T75.
PLAYBOOK = {
    "scan-infected-file": ("HIGH", "A scan found malware; the file went to quarantine.",
                           "Look in the quarantine bucket, find who uploaded it, do not release the file."),
    "scan-failed": ("MEDIUM", "The scanner could not reach a verdict.",
                    "Check scanner task logs (clamd down, S3 or queue error). Files stay in uploads and are retried."),
    "scan-backlog": ("MEDIUM", "Uploads are waiting too long for a scan.",
                     "Is the scanner task running? Check the ECS service desired/running count."),
    "scan-dead-letter": ("HIGH", "A scan request failed 3 times and is in the dead-letter queue.",
                         "Read the DLQ message, check the file, re-drive only after the cause is fixed."),
    "audit-write-failed": ("HIGH", "The audit log could not be written, so requests are being refused.",
                           "Check DynamoDB audit table health and the task role permissions."),
    "denied-requests-spike": ("HIGH", "Many requests were denied in a short time.",
                              "Check caller ids in the logs: attack, or a broken client sending wrong roles?"),
    "records-dependency-errors": ("MEDIUM", "The records service is failing calls to AWS services.",
                                  "Check DynamoDB and interface endpoint status, then task logs."),
    "records-memory-high": ("LOW", "Records task memory is high.", "Check for a leak; consider a bigger task size."),
    "scanner-memory-high": ("LOW", "Scanner task memory is high.", "ClamAV needs headroom; consider a bigger task size."),
    "records-alb-5xx": ("HIGH", "The load balancer is returning server errors.", "Check records task logs and target health."),
    "records-unhealthy-targets": ("HIGH", "No healthy records service targets.", "Check task state and /health/ready output."),
    "waf-blocked-requests": ("MEDIUM", "The web firewall is blocking many requests.", "Check WAF logs for source and rule."),
}
DEFAULT = ("MEDIUM", "Alarm without a playbook entry.", "Open the alarm in CloudWatch and check its metric.")


def classify(alarm_name):
    """Return (severity, meaning, first_step, matched_suffix_or_None)."""
    for suffix, (sev, meaning, step) in PLAYBOOK.items():
        if alarm_name.endswith(suffix):
            return sev, meaning, step, suffix
    return (*DEFAULT, None)


def build_message(event, env_name="securecare"):
    detail = event.get("detail", {})
    name = detail.get("alarmName", "unknown-alarm")
    state = detail.get("state", {})
    sev, meaning, step, suffix = classify(name)
    reason = state.get("reason", "")
    subject = f"[{sev}] {env_name}: {name}"[:100]  # SNS subject limit is 100 characters
    lines = [
        f"Incident: {name}",
        f"Severity: {sev}",
        f"Time: {event.get('time', 'unknown')}",
        f"Region: {event.get('region', 'unknown')}",
        f"Meaning: {meaning}",
        f"First step: {step}",
        f"Reason: {reason}",
        "Playbook entry: " + (suffix if suffix else "none (default text used)"),
    ]
    return subject, "\n".join(lines)


def handler(event, context=None, client=None):
    detail = event.get("detail", {})
    if detail.get("state", {}).get("value") != "ALARM":
        return {"published": False, "why": "not an ALARM transition"}
    subject, message = build_message(event, os.environ.get("ENV_NAME", "securecare"))
    sns = client or boto3.client("sns")
    sns.publish(TopicArn=os.environ["TOPIC_ARN"], Subject=subject, Message=message)
    print(json.dumps({"event": "incident_published", "subject": subject}))
    return {"published": True, "subject": subject}
