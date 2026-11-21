# Demo rehearsal runbook (T90)

Purpose: a 10-minute demonstration that the platform blocks bad uploads and bad images, with nothing left running afterwards.
Rehearse the whole thing once without an audience. The stack costs money while applied, so the demo is a **recorded or time-boxed session**, not an always-on environment.

## Before (15 minutes, outside the demo)

1. `cd securecare/infra/examples/phase1-plan-only`, then `terraform apply -var deploy=true -var interface_endpoints=true` (add `-var xray=true` only if tracing is part of the story and the daemon image was mirrored).
2. Wait until the records service and the scanner show 1 running task each.
3. From `infra/examples/phase1-plan-only`: `terraform output -json | python3 ../../scripts/make_gate3_env.py > ../../gate3.env`.
4. Open: CloudWatch dashboard `securecare-overview`, the quarantine bucket, the alarm email inbox, and a terminal in `infra/`.
5. Run all commands in the table from `infra/`. Have Docker and Trivy ready (needed for check 2).

## Demo script

| Min | What you do | What the audience sees | Say |
| --- | --- | --- | --- |
| 0-1 | Show the architecture page and the dashboard | The 11 alarms and the metrics | "No internet path, everything private; all alerts go to one place." |
| 1-4 | `bash scripts/gate3_runner.sh 1` from `infra/` (uploads the harmless EICAR test string) | File lands in uploads, then moves to quarantine; `securecare-scan-infected-file` goes to ALARM; alert email arrives with severity and first step | "EICAR is a standard test string, not real malware. Detection took seconds." |
| 4-6 | `bash scripts/gate3_runner.sh 4` (set `PROBE_LAMBDA` in `gate3.env` for the inside call) | Outside call fails, inside call works | "The service is reachable only from inside the VPC." |
| 6-8 | `bash scripts/gate3_runner.sh 2` (known-bad image blocked, our two images pass) | Bad image blocked with a list of fixable findings; ours pass | "A vulnerable image never reaches the registry." |
| 8-9 | `python3 scripts/recovery_time.py --cluster securecare --service securecare-records --yes` | A task is stopped and replaced (our last run: 38.3 s to running, 51.1 s healthy) | "Self-healing; the numbers are from one run." |
| 9-10 | Show `terraform destroy` finishing | Resources gone | "Nothing stays running; the base stack costs almost nothing." |

## After (always)

1. `terraform apply -var deploy=false -var interface_endpoints=false` (or `terraform destroy` if the base stack is not needed).
2. Check in the console that no ECS service, NAT, interface endpoint or load balancer is left in eu-west-1.
3. Empty the quarantine bucket of the EICAR test file only if the rehearsal notes ask for it; keep the evidence folder.

## Failure plan (say it before it happens)

- Alert email late: show the alarm state in CloudWatch instead; the alarm is the proof, the email is a delivery step.
- Scanner slow to start: pre-start it 10 minutes earlier; Fargate image pulls from ECR over the interface endpoints.
- Anything unexpected on stage: stop, say so, show the recorded evidence in `infra/evidence/gate3-2026-11-20`.
