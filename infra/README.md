# SecureCare infrastructure (network T11, containers T22, monitoring T60/T62)

Draft written by Container for the Day 2 network step with the Lead. **Nothing here has been applied to AWS.** It has not been run through `terraform validate` either (the workspace that wrote it has no Terraform); only the HCL syntax was parsed.

## Try it

```bash
cd examples/network-only
terraform init
terraform validate
terraform plan
```

Read the plan before applying. With `create_interface_endpoints = false` everything in it is free of hourly charges: 1 VPC, 2 subnets, 1 route table, 5 security groups with rules, 2 gateway endpoints, and the default security group closed.

## What to check in the plan

- No internet gateway, NAT gateway, elastic IP or `0.0.0.0/0` route.
- The VPC range is 10.20.0.0/16 and does not overlap the default VPC.
- Only the Lambda group can reach the load balancer; only the load balancer can reach the records service.

## Open points for the Lead

1. **Does the baseline (T12) already create a VPC?** If it does, call only the parts you need from this module, so we do not end up with two.
2. **Lambda to load balancer uses HTTP on port 80** inside the private network. If the compliance matrix needs encryption in transit on that hop too, we add an HTTPS listener with a certificate (needs an internal name); say so before T52.
3. **Lambda egress only allows the load balancer.** API adds rules for anything else their Lambdas call (their own endpoints) when they move into the VPC (T53).
4. **Interface endpoints** are a single zone to save cost. If a task in the other zone pulls images, traffic crosses zones (small cost, works).

## Files

| File | Purpose |
| --- | --- |
| `modules/network/main.tf` | VPC, private subnets, route table, gateway endpoints, security groups and rules, optional interface endpoints |
| `modules/network/variables.tf` | Region, ranges, the interface endpoint switch |
| `modules/network/outputs.tf` | IDs other modules (ECS, scanner, API) consume |
| `docs/decisions-network.md` | The text to paste into `docs/decisions.md` |


## Phase 1 modules (added 2026-11-10, code only, never applied)

| Module | What it creates | Hourly cost while `deploy = false` |
| --- | --- | --- |
| `modules/ecs_cluster` | ECS cluster (Fargate) | none |
| `modules/records_service` | 3 DynamoDB tables (on demand, point-in-time recovery), empty token secret, log group, execution and task roles, task definition. With `deploy = true` also an internal load balancer and the ECS service | none (secret about $0.40 a month) |
| `modules/scanner` | uploads, clean and quarantine buckets, scan queue and dead-letter queue, S3 notification, roles, task definition. With `deploy = true` also the ECS service | none |
| `modules/monitoring` | alarm topic, 11 alarms (4 only when `services_deployed = true`), overview dashboard | alarms about $0.10 each a month beyond the free 10 |
| `modules/waf_alarm` | WAF blocked-requests alarm and topic in us-east-1 | small |

Try the plan (no apply):

```bash
cd examples/phase1-plan-only
terraform init
terraform validate
terraform plan -out=phase1.plan
```

Check before anything is ever applied: no `aws_lb` and no `aws_ecs_service` in the plan, no `0.0.0.0/0` anywhere, no public bucket, and the secret has no value. Send the plan summary line (`Plan: N to add`) back so the offline reference check can be compared with the real one.

Offline check used while writing (no Terraform needed): `python3 tools/check_refs.py` parses every file and confirms that each variable, local, resource, module output and module argument exists. It does **not** replace `terraform validate`.

Before Phase 3: create the ECR repositories (T23), build and push both images (T24), set the token value with `aws secretsmanager put-secret-value`, add `extra_interface_endpoint_services = ["secretsmanager"]` to the network module call, then set `deploy = true` for a short test and destroy afterwards.

## Phase 3 additions (T74, T56, T76)

- `modules/incident_automation/` - EventBridge rule on CloudWatch alarm state changes (`securecare-` alarms moving to ALARM) -> Lambda (`lambda/handler.py`) -> the existing SNS topic with a message that says severity, meaning and first step. Read-only: it never changes resources. Playbook texts in `PLAYBOOK` are placeholders for the team to edit.
- `scripts/prove_isolation.py` (T56) - internal ALB, no open security group rule, no internet gateway, outside call fails; with `--probe-lambda` also proves the inside call works. `scripts/probe_lambda.py` is the throwaway probe.
- `scripts/recovery_time.py` (T76) - stops one task of a `securecare*` service and times the replacement; dry run unless `--yes`.
- `scripts/image_gate.py` - the image gate for the pipeline: fails only on HIGH/CRITICAL findings that have a fix; findings with no fix are counted and listed but do not block. Runs Trivy itself (`--image`) or reads a Trivy JSON report (`--report`). Exit 0 pass, 1 blocked, 2 could not run (never a silent pass). Optional `--accept-file` lets a fixable finding through until a date, with a written reason.
- `scripts/gate3_runner.sh` + `scripts/gate3.env.example` - runs the Gate 3 evidence commands and saves each output into `evidence/gate3/` with account ids replaced. Default is the read-only checks (2, 3, 4, 6, 7); `bash scripts/gate3_runner.sh 1` uploads the EICAR test file; `--list` shows what each check does. Screenshots stay manual and the summary file lists which ones are missing.
- `examples/phase1-plan-only/` is now applyable on its own: own VPC, two ECR repositories, records service, scanner, monitoring, incident automation and (only while `deploy = true`) a throwaway probe Lambda. Variables: `deploy` and `interface_endpoints` (both false by default; they switch on everything that bills by the hour) and `alarm_email`. `outputs.tf` feeds `scripts/make_gate3_env.py`, which writes `gate3.env` from `terraform output -json`.
- `tests/` - `python3 -m pytest tests -q` (37 tests, no AWS needed; the runner is tested with fake `aws`, `curl` and `trivy`).
