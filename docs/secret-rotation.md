# Runbook: rotate the internal token (T105)

The records service only accepts calls that carry the internal token. This runbook changes the token with no
downtime: for a short time the service accepts both the new and the previous value, then only the new one.

**Who:** project owner (container and monitoring). The caller (the API Lambda) belongs to its owner; step 5 needs them.
**When:** on a schedule agreed with DevSecOps (write the date in the README), and at once if the token may have leaked.
**Cost rule:** the tasks must be running for steps 3 to 6. Do it inside a normal test session (`deploy=true`) and switch it off afterwards.

## What makes this work

- The service reads `INTERNAL_TOKEN` and, if present, `INTERNAL_TOKEN_PREVIOUS` (`app/auth.py`). A request is accepted if it carries either.
- Secrets Manager keeps the last value under the stage `AWSPREVIOUS`. The task definition can inject it as
  `INTERNAL_TOKEN_PREVIOUS` when the Terraform variable `inject_previous_token` is `true`.
- **Important:** `AWSPREVIOUS` exists only after the second `put-secret-value`. If it is set before that, the task fails to start.
  That is why the variable is off by default.

## Steps

1. **Make a new token** (never paste it in chat, tickets or commits):
   `NEW=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")`
2. **Store it as the new current value:**
   `aws secretsmanager put-secret-value --region eu-west-1 --secret-id securecare/records/internal-token --secret-string "$NEW"`
   The old value is now `AWSPREVIOUS`. Check: `aws secretsmanager describe-secret --region eu-west-1 --secret-id securecare/records/internal-token --query VersionIdsToStages`
   should list both `AWSCURRENT` and `AWSPREVIOUS`.
3. **Let the service accept both:** `terraform apply -var deploy=true -var interface_endpoints=true -var inject_previous_token=true`
   then restart the tasks so they read the secret: `aws ecs update-service --region eu-west-1 --cluster securecare --service securecare-records --force-new-deployment`
   Wait until the new task is healthy (`/health/ready`).
4. **Prove both values work** with the probe or a test call: the old token and the new token must both be accepted; a made-up token must give 401.
5. **Switch the caller to the new token** (API Lambda owner). Run the end-to-end test again; it must still pass.
6. **Retire the old value:** `terraform apply ... -var inject_previous_token=false`, then force a new deployment again.
   Check the old token now gives 401.
7. **Record it:** date, who did it, and that steps 4 and 6 passed. Do not record the token values.

## If something goes wrong

- Task will not start after step 3: the secret has no `AWSPREVIOUS` yet. Set `inject_previous_token=false`, apply, and repeat step 2.
- Callers get 401 after step 5: the caller still has the old token. While both values are accepted nothing is lost; fix the caller first, retire the old value later.
- Token leaked: run steps 1, 2, 3, 5, 6 without waiting. Between steps 3 and 6 the old token still works, so keep that window short.

## Not covered

Automatic rotation (a rotation Lambda on a schedule) is not built. It would also need the caller to read the secret instead of holding a copy. Out of scope for this project; this manual runbook is the agreed approach.
