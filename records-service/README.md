# SecureCare records service (T09: local skeleton)

Container-owner skeleton for Day 1. It has two health endpoints and a DynamoDB connection.
Visit notes, audit entries and input validation come later in T54.

## Run it on your computer

Needs Docker Desktop (or Docker Engine with the Compose plugin).

```bash
docker compose up --build
```

In a second terminal:

```bash
curl http://localhost:8080/health          # {"status":"ok",...}
curl http://localhost:8080/health/ready    # {"status":"ready"}  (table reached)
```

Stop with Ctrl+C, then `docker compose down`. DynamoDB Local keeps data in memory only, so nothing is saved.

## Try the notes API by hand (T54 early version, synthetic data)

The local token and seed data are set in `docker-compose.yml`. The caller headers below are a **draft contract** that the API owner must confirm in T40.

```bash
H='-H X-Internal-Token:local-dev-token -H X-Caller-Id:doc-demo -H X-Caller-Role:doctor'
curl -s -X POST localhost:8080/patients/pat-demo/notes $H -H 'Content-Type: application/json' \
  -d '{"visit_date":"2026-11-09","text":"Synthetic note for testing."}'
curl -s localhost:8080/patients/pat-demo/notes $H                      # list, newest first
curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/patients/pat-demo/notes   # 401: no token
curl -s -w '\n%{http_code}\n' localhost:8080/patients/pat-other/notes $H         # 403: not assigned
docker compose logs records | grep '"event"\|AuditWriteFailures\|DeniedRequests'  # JSON logs and metric lines
```

Rules the service enforces: valid token, known role, a doctor or nurse needs an assignment to the patient, a patient reads only their own notes, an audit entry is written **before** every allowed action (if it cannot be written, the action is refused with 503), note text never appears in logs or audit entries, and errors never echo the input.

## Prove the container is locked down (evidence for Compliance)

Run these while it is up and save a screenshot of the output:

```bash
docker compose exec records id                     # uid=10001 gid=10001 (not root)
docker compose exec records touch /srv/test        # fails: read-only file system
docker compose exec records sh -c 'cat /proc/self/status | grep CapEff'   # CapEff: 0000000000000000
```

## Run the tests (no Docker needed)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

## Files

| File | Purpose |
| --- | --- |
| `app/main.py` | `/health`, `/health/ready`, and the notes routes (`POST/GET /patients/{id}/notes`, `GET .../notes/{note_id}`). API docs are switched off. |
| `app/auth.py` | Token check (two values accepted for rotation), caller headers, assignment rules. Fails closed if no token is set. |
| `app/audit.py`, `app/obs.py` | Audit entries written before the action; JSON logs and CloudWatch EMF metrics (`DeniedRequests`, `AuditWriteFailures`, `NotesCreated`, `ValidationFailures`, `AwsErrors`). |
| `app/models.py` | Input validation: id patterns, 1 to 5000 characters, no control characters, unknown fields rejected. |
| `app/db.py`, `app/config.py` | DynamoDB access (notes, audit, assignments tables) and settings from environment variables. No secrets in code. |
| `Dockerfile` | Multi-stage build, user 10001, no writable paths, no curl, Python health check. |
| `docker-compose.yml` | The service plus DynamoDB Local, with read-only filesystem, all capabilities dropped, no-new-privileges. |
| `app/tracing.py` | Optional X-Ray tracing, off unless `XRAY_ENABLED=true`. Route template, status and DynamoDB operation/table only; never identifiers, tokens or bodies; never breaks a request. |
| `tests/` | 33 tests using a fake DynamoDB (moto): health, auth, authorization, audit fail-closed, validation, tracing. |

## Notes for later tasks

- T24/T25: the Dockerfile should pass Trivy; run `trivy image securecare-records:dev` before pushing.
- T55: the same restrictions in `docker-compose.yml` become `readonlyRootFilesystem`, `user: 10001`, and `linuxParameters.capabilities.drop = ["ALL"]` in the ECS task definition. The load balancer health check path is `/health`.
- In AWS, leave `DYNAMODB_ENDPOINT`, `CREATE_TABLE_ON_START` and `SEED_LOCAL_DATA` unset; Terraform creates the three tables (`RECORDS_TABLE`, `AUDIT_TABLE`, `ASSIGNMENTS_TABLE`). `INTERNAL_TOKEN` (and `INTERNAL_TOKEN_PREVIOUS` during rotation) must come from Secrets Manager through the task definition `secrets` list, never `environment`.
- Open points for T40/T54: header names and roles are drafts; who fills the assignments table (API or an admin flow) is undecided; the table encryption key (KMS) and point-in-time recovery are set in Terraform (T22/T55), not in this code; request body size is capped by the 5000-character limit but a proxy-level limit is still advisable.
