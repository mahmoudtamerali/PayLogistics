#!/usr/bin/env python3
"""T56: evidence that the records service is NOT reachable from the internet but IS reachable from inside.

Read-only except for one optional Lambda invoke. Run it while the stack is applied (ALB exists), then destroy the stack.

  python3 scripts/prove_isolation.py --vpc-id vpc-... --alb-name securecare-records \
      [--probe-lambda <name-of-a-lambda-in-the-private-subnets>] [--out evidence/gate3/56-isolation.json]

Checks:
  1. ALB scheme is "internal"                           (aws elbv2)
  2. No security group in the VPC allows 0.0.0.0/0 or ::/0 inbound
  3. The VPC has no internet gateway
  4. A request from THIS computer to the ALB DNS name fails (timeout, DNS failure or refused)
  5. (optional) a Lambda inside the VPC gets HTTP 200 from /health through the ALB
Exit code: 0 all passed, 1 something failed, 3 nothing failed but a check was SKIPPED (not proven).
"""
import argparse
import json
import socket
import sys
import urllib.error
import urllib.request

import boto3

REGION = "eu-west-1"


def open_cidrs(security_groups):
    """Return [(group_id, cidr)] for every inbound rule open to the whole internet."""
    found = []
    for sg in security_groups:
        for perm in sg.get("IpPermissions", []):
            for r in perm.get("IpRanges", []):
                if r.get("CidrIp") == "0.0.0.0/0":
                    found.append((sg["GroupId"], "0.0.0.0/0"))
            for r in perm.get("Ipv6Ranges", []):
                if r.get("CidrIpv6") == "::/0":
                    found.append((sg["GroupId"], "::/0"))
    return found


def outside_call(url, timeout=10):
    """Return (reachable, detail). reachable=True means the service answered from outside = BAD."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (our own URL)
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"          # something answered: it is reachable
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def run(args, ec2=None, elbv2=None, lam=None, call=outside_call):
    ec2 = ec2 or boto3.client("ec2", region_name=REGION)
    elbv2 = elbv2 or boto3.client("elbv2", region_name=REGION)
    results = []

    def add(name, status, detail):
        results.append({"check": name, "status": status, "detail": detail})

    lbs = elbv2.describe_load_balancers(Names=[args.alb_name])["LoadBalancers"]
    lb = lbs[0]
    add("alb_scheme_internal", "PASS" if lb["Scheme"] == "internal" else "FAIL", f"scheme={lb['Scheme']}")

    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [args.vpc_id]}])["SecurityGroups"]
    bad = open_cidrs(sgs)
    add("no_inbound_from_internet", "PASS" if not bad else "FAIL", f"open rules: {bad}" if bad else f"{len(sgs)} groups checked")

    igws = ec2.describe_internet_gateways(
        Filters=[{"Name": "attachment.vpc-id", "Values": [args.vpc_id]}])["InternetGateways"]
    add("no_internet_gateway", "PASS" if not igws else "FAIL", f"{len(igws)} attached")

    reachable, detail = call(f"http://{lb['DNSName']}/health")
    add("outside_call_fails", "FAIL" if reachable else "PASS", detail)

    if args.probe_lambda:
        lam = lam or boto3.client("lambda", region_name=REGION)
        payload = json.dumps({"url": f"http://{lb['DNSName']}/health"}).encode()
        out = lam.invoke(FunctionName=args.probe_lambda, Payload=payload)
        body = json.loads(out["Payload"].read() or b"{}")
        ok = out.get("StatusCode") == 200 and "FunctionError" not in out and body.get("status") == 200
        add("inside_call_works", "PASS" if ok else "FAIL", json.dumps(body)[:300])
    else:
        add("inside_call_works", "SKIPPED", "no --probe-lambda given; this half is not proven")

    return results


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vpc-id", required=True)
    p.add_argument("--alb-name", required=True)
    p.add_argument("--probe-lambda")
    p.add_argument("--out")
    args = p.parse_args(argv)
    results = run(args)
    for r in results:
        print(f"{r['status']:8} {r['check']:28} {r['detail']}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
        print("saved", args.out)
    if any(r["status"] == "FAIL" for r in results):
        return 1
    if any(r["status"] == "SKIPPED" for r in results):
        return 3  # nothing failed, but the inside-call half is not proven
    return 0


if __name__ == "__main__":
    sys.exit(main())
