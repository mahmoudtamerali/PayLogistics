#!/usr/bin/env python3
"""Write gate3.env from `terraform output -json`, so nothing is copied by hand.

  cd examples/phase1-plan-only && terraform output -json | python3 ../../scripts/make_gate3_env.py > ../../gate3.env

Reads JSON on stdin, prints the env file on stdout. Names that Terraform does not output (alarm, dashboard,
cluster, task definition families) are fixed by the modules and are written as they are."""
import json
import sys


def build(outputs):
    def val(key, default=""):
        return (outputs.get(key) or {}).get("value", default) or default

    buckets = val("bucket_names", {})
    lines = [
        "REGION_CHECK=eu-west-1",
        f"UPLOADS_BUCKET={buckets.get('uploads', '')}",
        f"QUARANTINE_BUCKET={buckets.get('quarantine', '')}",
        "ALARM_INFECTED=securecare-scan-infected-file",
        'REAL_IMAGES="securecare-records:test securecare-scanner:dev"',
        'TASKDEFS="securecare-records securecare-scanner"',
        'LOCAL_IMAGES="securecare-records:test securecare-scanner:dev"',
        f"VPC_ID={val('vpc_id')}",
        "ALB_NAME=securecare-records",
        "CLUSTER=securecare",
        "DASHBOARD=securecare-overview",
    ]
    if val("probe_lambda"):
        lines.append(f"PROBE_LAMBDA={val('probe_lambda')}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(build(json.load(sys.stdin)))
