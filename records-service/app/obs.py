"""Logging and metrics helpers.

Everything goes to stdout as one JSON object per line. In AWS the ECS task's
awslogs driver ships stdout to CloudWatch Logs, and CloudWatch turns lines
that follow the Embedded Metric Format (EMF) into metrics. That means custom
metrics need no PutMetricData call and no extra VPC endpoint (see T60).

Rule: never log note text, tokens or patient names. Identifiers only.
"""
import json
import sys
import time

from . import config

NAMESPACE = "SecureCare/Records"


def _emit(obj: dict):
    sys.stdout.write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def log(event: str, **fields):
    _emit({"ts": time.time(), "service": config.SERVICE_NAME, "event": event, **fields})


def metric(name: str, value: float = 1, unit: str = "Count", **properties):
    """Emit one EMF metric line. `properties` are searchable log fields only
    (not metric dimensions), so cost stays at one metric per name."""
    _emit(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [["Service"]],
                        "Metrics": [{"Name": name, "Unit": unit}],
                    }
                ],
            },
            "Service": config.SERVICE_NAME,
            name: value,
            **properties,
        }
    )
