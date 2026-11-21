"""JSON logs and CloudWatch Embedded Metric Format lines on stdout.

No PutMetricData call and no CloudWatch endpoint needed: the awslogs driver
ships stdout and CloudWatch extracts the metrics (T60). Log object keys only
(they are random ids by design), never file contents or patient names.
"""
import json
import sys
import time

from . import config


def _emit(obj: dict):
    sys.stdout.write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()


def log(event: str, **fields):
    _emit({"ts": time.time(), "service": config.SERVICE_NAME, "event": event, **fields})


def metric(name: str, value: float = 1, unit: str = "Count", **properties):
    _emit(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": config.NAMESPACE,
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
