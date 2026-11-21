#!/usr/bin/env python3
"""T76: how long does the records service take to recover when one task dies?

Stops ONE running task of an ECS service and times the recovery:
  - seconds until a NEW task is RUNNING
  - seconds until the load balancer reports a healthy target again (if --target-group-arn is given)

  python3 scripts/recovery_time.py --cluster securecare --service securecare-records --yes \
      [--target-group-arn arn:...] [--out evidence/gate3/76-recovery.json]

Safety: refuses clusters or services that are not ours (names must start with "securecare"), and does nothing
without --yes. It stops a task; ECS starts a replacement because the service wants desired_count tasks.
Run it only while the stack is applied for a test, then destroy the stack.
"""
import argparse
import json
import sys
import time

import boto3

REGION = "eu-west-1"
PREFIX = "securecare"


def guard(cluster, service):
    for label, value in (("cluster", cluster), ("service", service)):
        if not value.startswith(PREFIX):
            raise SystemExit(f"refusing: {label} '{value}' does not start with '{PREFIX}' (not ours)")


def wait_for(check, timeout, interval, clock=time.monotonic, sleep=time.sleep):
    """Return seconds until check() is true, or None on timeout. check gets no args."""
    start = clock()
    while clock() - start < timeout:
        if check():
            return round(clock() - start, 1)
        sleep(interval)
    return None


def run(args, ecs=None, elbv2=None, clock=time.monotonic, sleep=time.sleep):
    guard(args.cluster, args.service)
    ecs = ecs or boto3.client("ecs", region_name=REGION)
    tasks = ecs.list_tasks(cluster=args.cluster, serviceName=args.service, desiredStatus="RUNNING")["taskArns"]
    if not tasks:
        raise SystemExit("no running task in this service; nothing to stop")
    victim, old = tasks[0], set(tasks)
    old_targets = set()
    if args.target_group_arn:
        elbv2 = elbv2 or boto3.client("elbv2", region_name=REGION)
        old_targets = {d["Target"]["Id"] for d in
                       elbv2.describe_target_health(TargetGroupArn=args.target_group_arn)["TargetHealthDescriptions"]}
    t0 = clock()
    ecs.stop_task(cluster=args.cluster, task=victim, reason="T76 recovery-time test")

    def new_task_running():
        now = set(ecs.list_tasks(cluster=args.cluster, serviceName=args.service, desiredStatus="RUNNING")["taskArns"])
        fresh = now - old
        if not fresh:
            return False
        desc = ecs.describe_tasks(cluster=args.cluster, tasks=list(fresh))["tasks"]
        return any(t["lastStatus"] == "RUNNING" for t in desc)

    running_s = wait_for(new_task_running, args.timeout, args.interval, clock, sleep)
    result = {"stopped_task": victim.split("/")[-1], "new_task_running_seconds": running_s,
              "target_healthy_seconds": None}

    if args.target_group_arn:
        # A target that was healthy BEFORE the stop must not count: the load balancer needs a while to notice it is gone.
        def healthy():
            th = elbv2.describe_target_health(TargetGroupArn=args.target_group_arn)["TargetHealthDescriptions"]
            return any(d["TargetHealth"]["State"] == "healthy" and d["Target"]["Id"] not in old_targets for d in th)

        h = wait_for(healthy, args.timeout, args.interval, clock, sleep)
        result["target_healthy_seconds"] = None if h is None else round(clock() - t0, 1)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cluster", required=True)
    p.add_argument("--service", required=True)
    p.add_argument("--target-group-arn")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--interval", type=float, default=3)
    p.add_argument("--out")
    p.add_argument("--yes", action="store_true", help="really stop a task")
    args = p.parse_args(argv)
    guard(args.cluster, args.service)
    if not args.yes:
        print("dry run: would stop one running task of", args.service, "in", args.cluster, "(add --yes)")
        return 0
    result = run(args)
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
    return 0 if result["new_task_running_seconds"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
