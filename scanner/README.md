# SecureCare scanner (T59 early version, code only)

Takes each new file in the **uploads** bucket (announced through SQS), scans it with ClamAV, and moves it to the **clean** bucket or the **quarantine** bucket. A file is never moved to clean without a clean verdict. Nothing here has been deployed; see "What is not verified".

## How it works

1. S3 sends an `ObjectCreated` event to the SQS queue.
2. `scanner/worker.py` reads one message, checks the size, streams the object from S3 straight into clamd (nothing is written to disk).
3. Clean: copy to the clean bucket. Infected or too large: copy to quarantine. Then the original is deleted from uploads and the SQS message is deleted.
4. Any failure (clamd down, S3 error, unknown reply): the message stays in the queue, SQS redelivers it, and after a few tries it lands in the dead-letter queue. The file stays in uploads, which users cannot read.
5. Metrics (CloudWatch Embedded Metric Format, namespace `SecureCare/Scanner`): `ScansClean`, `ScansInfected`, `ScansTooLarge`, `ScansFailed`, `ScanDurationMs`. Alarms on these are in the monitoring module (T60).

`scanner/main.py` starts clamd (config written to `/tmp`), waits until it answers, then runs the worker. If clamd stops, the container exits and ECS restarts it. The health check passes only while the worker loop is running.

## Decision needed: signature updates (recommendation: bake into the image)

The task has no internet and no NAT gateway (decision in the network module), so ClamAV cannot download new virus definitions while running. Options:

| Option | Cost | Problem |
| --- | --- | --- |
| **A. Bake into the image, rebuild weekly (recommended)** | Free | Definitions are up to a week old unless you rebuild more often. Needs a scheduled CI job (DevSecOps) that rebuilds, pushes to ECR, and forces a new deployment. |
| B. Mirror definitions into S3 with a scheduled job, scanner loads them at start | Small | More moving parts: a job with internet access, a bucket, and a reload step. |
| C. Add a NAT gateway so freshclam can run | About 1 USD per day in credits | Breaks the "no internet" safety rule for the most exposed container. Not recommended. |

Pick A for the project. Write the date in the image (`/etc/signatures-built-at`) and show it in the README for the demo. Say in the report that definitions age between rebuilds.

## Run the tests (no Docker, no AWS)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The tests use a fake clamd and a fake S3/SQS, so they prove the logic (clean, infected, too large, clamd failure never releases a file, unknown messages dropped), not ClamAV itself.

## Try it with the real ClamAV, locally (needs Docker and internet for the build)

```bash
docker build --platform linux/amd64 -t securecare-scanner:dev .
docker run --rm --platform linux/amd64 securecare-scanner:dev          # exits 2: settings missing (expected)
```

To prove real detection without AWS, build the image, start a shell and scan the EICAR test file:

```bash
python make_eicar.py eicar-test.txt        # your antivirus may delete it: that is normal
docker run --rm --platform linux/amd64 -v "$PWD/eicar-test.txt:/tmp/e.txt:ro" --entrypoint clamscan securecare-scanner:dev /tmp/e.txt
# expected: /tmp/e.txt: Eicar-Signature FOUND
```

Save a screenshot as evidence for T59. The end-to-end version (upload to S3, see it land in quarantine) is done in Phase 3 after deployment, with the resources switched off straight afterwards.

## What is not verified

- **Proven on AWS (test session 2026-11-20):** the image builds, clamd starts with the baked signatures (no internet), and an EICAR file uploaded to the uploads bucket was detected (`Eicar-Signature FOUND`, 234 ms), moved to quarantine, and raised the `securecare-scan-infected-file` alarm. The task ran with 2 vCPU / 4 GB without being killed. Still unproven: memory under load (only a single file was scanned), and real files near the `MAX_BYTES` limit.
- **Fargate `/tmp` ownership (found in that session, fixed in the Dockerfile):** Fargate creates the scratch volume owned by root, so the non-root scanner got `PermissionError` on `/tmp/clamd.conf` and restarted in a loop. The image now owns `/tmp` for uid 10001 and declares it a `VOLUME`; with that change the task started cleanly. Do not remove those two lines.
- Objects larger than `MAX_BYTES` (25 MB default) are quarantined unscanned. Confirm the upload limit with the API owner so legitimate files are not rejected.
- Object keys must be random ids (no patient names), because keys appear in logs.
- Debian's packaged ClamAV can lag the newest release. If Trivy (T25) flags it, switch the base to the official `clamav/clamav` image and keep the same worker.

## Task definition notes for T55

- Root filesystem read-only; mount a volume at `/tmp` (Fargate has no tmpfs).
- `user: 10001`, drop all capabilities, no ports, no load balancer.
- Environment: `UPLOADS_BUCKET`, `CLEAN_BUCKET`, `QUARANTINE_BUCKET`, `QUEUE_URL`, `AWS_REGION`, optional `MAX_BYTES`.
- Queue visibility timeout at least 300 seconds (longer than the slowest scan); dead-letter queue after 3 receives.
- Task role: read and delete on uploads, put on clean and quarantine, receive and delete on the queue, and nothing else.
