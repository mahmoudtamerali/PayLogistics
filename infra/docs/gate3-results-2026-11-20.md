# Gate 3 test session results (2026-11-20)

Container and monitoring owner. Region eu-west-1. One session with `deploy = true`, about 20 minutes
(18:12 to 18:33 Cairo time): apply (19 resources added), tests, destroy (18 removed).
Check 7 passed after the destroy. Estimated cost about $0.08 (approximate prices; confirm on the
Billing Free Tier page the next morning).

| Check | Result |
|---|---|
| 4 isolation | PASS: ALB internal, call from outside fails, no rule open to the world, no internet gateway, probe Lambda inside the VPC reached it |
| 1 EICAR | `Eicar-Signature FOUND` in 234 ms; file left uploads, is in quarantine; `securecare-scan-infected-file` went to ALARM. The runner printed FAIL at 90 s only because the alarm evaluates every 5 minutes |
| 3 containers | PASS: non-root, read-only root filesystem, capabilities dropped, no secret-like environment variables |
| 6 dashboard | PASS: 11 alarms plus scan and queue metrics |
| Recovery (T76) | Task stopped on purpose: new task running in 38.3 s, target healthy in 51.1 s (one run; two are asked for) |
| Incident automation (T74) | `set-alarm-state` on `securecare-denied-requests-spike`: the Lambda published the [HIGH] message in 331 ms, both emails arrived. A simulated alarm, not a real attack |
| 7 nothing costly running | PASS after the destroy |

## Found in the session

The scanner restarted in a loop: `PermissionError: /tmp/clamd.conf`. Fargate creates the scratch volume
owned by root and the scanner runs as uid 10001. Fix in `scanner/Dockerfile`: `chown 10001:10001 /tmp`
and `VOLUME ["/tmp"]` (before the `USER` line). After rebuild and redeploy the log showed `clamd_ready`
and `worker_started`. Do not remove those two lines.

Setup note for the Mac: `prove_isolation.py` needs `pip install boto3 "botocore[crt]"` after `aws login`.

## Still unproven

- Memory under real load (one small file was scanned; 2 vCPU and 4 GB were enough for that).
- Recovery time repeated twice.
- The image gate inside the real pipeline (check 2 screenshots), the weekly signature rebuild job.
- Whether the stack must move to a VPC supplied by the Lead (T12); this stack builds its own
  (vpc with two private subnets, no NAT, no internet gateway).

## Evidence files

`evidence/gate3/` on the owner's Mac (copy in `~/Desktop/depiproject/gate3-evidence-2026-11-20`):
01-*, 03-*, 04-*, 06-*, 07-*, recovery.txt, 00-summary.txt. Screenshots: alarm in ALARM, dashboard
(scan results and alarm panel), load balancers list. Still to save: the [HIGH] incident email.
