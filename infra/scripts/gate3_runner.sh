#!/usr/bin/env bash
# Gate 3 evidence runner: runs the evidence commands and saves each output into evidence/gate3/.
#
#   cp scripts/gate3.env.example gate3.env      # fill in the values from terraform output, once
#   bash scripts/gate3_runner.sh                # checks 2, 3, 4, 6, 7 (read-only)
#   bash scripts/gate3_runner.sh 1              # check 1 only: uploads the EICAR test file, waits, reads results
#   bash scripts/gate3_runner.sh 3 4            # any list of checks
#   bash scripts/gate3_runner.sh --list         # show what each check does, run nothing
#
# Only check 1 changes anything in AWS (one harmless test file into the uploads bucket, which the scanner
# then moves). Every other check only reads. Checks 5 (signature update text) and the screenshots in 1, 2,
# 6 and 7 are done by hand: the runner tells you which files are still missing.
#
# Every saved file has 12-digit account ids replaced by <ACCOUNT>. Still look through them before sharing.
# Exit code 0 when every check that ran passed, 1 if any failed, 2 if the setup is wrong.

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${GATE3_ENV:-gate3.env}"
OUT="${GATE3_OUT:-evidence/gate3}"
export AWS_REGION="eu-west-1" AWS_DEFAULT_REGION="eu-west-1" AWS_PAGER="" AWS_DEFAULT_OUTPUT="json"

PASS=0
FAIL=0
SUMMARY=""

list_checks() {
  cat <<'EOF'
1  EICAR upload is quarantined and the alarm history shows it     (writes one test file to S3, waits 90 s)
2  Vulnerable image is rejected, real images pass the image gate  (needs docker and trivy)
3  Task definitions: non-root, read-only, no secret in environment; docker uid check
4  Not reachable from the internet: ALB scheme, curl timeout, security group rules, no internet gateway,
   in-VPC call (the last one only if PROBE_LAMBDA is set)
6  Dashboard JSON and the metric list
7  Nothing costly left running (tasks, load balancers, interface endpoints, NAT gateways)
EOF
}

die() { echo "setup problem: $*" >&2; exit 2; }

redact() { sed -E 's/(^|[^0-9])[0-9]{12}([^0-9]|$)/\1<ACCOUNT>\2/g; s/(^|[^0-9])[0-9]{12}([^0-9]|$)/\1<ACCOUNT>\2/g'; }

need() {
  for v in "$@"; do
    [ -n "${!v:-}" ] || { echo "  missing $v in $ENV_FILE"; return 1; }
  done
}

record() { # record <label> <ok|fail|skip> <detail>
  case "$2" in
    ok)   PASS=$((PASS + 1));  SUMMARY="${SUMMARY}PASS  $1  $3"$'\n' ;;
    fail) FAIL=$((FAIL + 1));  SUMMARY="${SUMMARY}FAIL  $1  $3"$'\n' ;;
    skip) SUMMARY="${SUMMARY}SKIP  $1  $3"$'\n' ;;
  esac
  echo "  -> $2: $3"
}

# save <file> <command...>: run, store redacted output (stdout and stderr), return the command's exit code
save() {
  local file="$1"; shift
  "$@" 2>&1 | redact > "$OUT/$file"
  return "${PIPESTATUS[0]}"
}

# ------------------------------------------------------------------ check 1
check1() {
  echo "Check 1: EICAR quarantine"
  need UPLOADS_BUCKET QUARANTINE_BUCKET ALARM_INFECTED || { record 1 skip "config missing"; return; }
  local tmp; tmp="$(mktemp)"
  # the standard EICAR test string, built in two pieces so this script is not flagged as a virus
  local p1='X5O!P%@AP[4\PZX54(P^)7CC)7}$' p2='EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
  printf '%s%s\n' "$p1" "$p2" > "$tmp"
  local key="incoming/eicar-test-$(date +%Y%m%d-%H%M%S).txt"
  if ! save 01-eicar-upload.txt aws s3 cp "$tmp" "s3://$UPLOADS_BUCKET/$key"; then
    rm -f "$tmp"; record 1 fail "upload failed, see 01-eicar-upload.txt"; return
  fi
  rm -f "$tmp"
  echo "  waiting ${EICAR_WAIT:-90} s for the scanner"
  sleep "${EICAR_WAIT:-90}"
  save 01-quarantine-list.txt aws s3 ls "s3://$QUARANTINE_BUCKET/" --recursive
  save 01-uploads-after.txt aws s3 ls "s3://$UPLOADS_BUCKET/incoming/"
  save 01-alarm-history.txt aws cloudwatch describe-alarm-history --alarm-name "$ALARM_INFECTED" \
    --history-item-type StateUpdate --max-items 3
  local gone=1 quarantined=1 alarm=1
  grep -q "$(basename "$key")" "$OUT/01-uploads-after.txt" && gone=0
  [ -s "$OUT/01-quarantine-list.txt" ] && quarantined=0
  grep -q '"ALARM"' "$OUT/01-alarm-history.txt" && alarm=0
  if [ $gone -eq 1 ] && [ $quarantined -eq 0 ] && [ $alarm -eq 0 ]; then
    record 1 ok "file left uploads, is in quarantine, alarm went to ALARM (still take 01-alarm-in-alarm.png)"
  else
    record 1 fail "gone from uploads=$([ $gone -eq 1 ] && echo yes || echo NO), in quarantine=$([ $quarantined -eq 0 ] && echo yes || echo NO), alarm=$([ $alarm -eq 0 ] && echo yes || echo NO). The alarm can need a few more minutes: re-run only the alarm line."
  fi
}

# ------------------------------------------------------------------ check 2
check2() {
  echo "Check 2: image gate"
  need REAL_IMAGES || { record 2 skip "config missing"; return; }
  command -v trivy >/dev/null || { record 2 fail "trivy not installed"; return; }
  : > "$OUT/02-trivy-output.txt"
  local bad_image="${BAD_IMAGE:-python:3.8-slim}" ok=0 img
  echo "=== deliberately old image: must be BLOCKED ($bad_image) ===" | tee -a "$OUT/02-trivy-output.txt" >/dev/null
  python3 "$HERE/image_gate.py" --image "$bad_image" 2>&1 | redact | tee -a "$OUT/02-trivy-output.txt" >/dev/null
  if [ "${PIPESTATUS[0]}" -ne 1 ]; then ok=1; echo "  old image was NOT blocked" ; fi
  for img in $REAL_IMAGES; do
    echo "=== real image: must PASS ($img) ===" >> "$OUT/02-trivy-output.txt"
    python3 "$HERE/image_gate.py" --image "$img" 2>&1 | redact | tee -a "$OUT/02-trivy-output.txt" >/dev/null
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then ok=1; echo "  $img did not pass"; fi
  done
  if [ $ok -eq 0 ]; then record 2 ok "old image blocked, real images pass (screenshots 02-gate-fail.png and 02-gate-pass.png come from the pipeline run)"
  else record 2 fail "see 02-trivy-output.txt"; fi
}

# ------------------------------------------------------------------ check 3
check3() {
  echo "Check 3: non-root, read-only, no secrets in environment"
  need TASKDEFS || { record 3 skip "config missing"; return; }
  : > "$OUT/03-taskdef.json"
  local td failed=0
  for td in $TASKDEFS; do
    save "03-taskdef-$td.json" aws ecs describe-task-definition --task-definition "$td" \
      --query 'taskDefinition.containerDefinitions[].{name:name,user:user,readonlyRootFilesystem:readonlyRootFilesystem,environment:environment,secrets:secrets,caps:linuxParameters.capabilities}' \
      || { echo "  could not read $td"; failed=1; continue; }
    cat "$OUT/03-taskdef-$td.json" >> "$OUT/03-taskdef.json"
    python3 - "$OUT/03-taskdef-$td.json" <<'PY' || failed=1
import json, re, sys
bad = []
for c in json.load(open(sys.argv[1])):
    if str(c.get("user") or "").split(":")[0] in ("", "0", "root"):
        bad.append(f"{c['name']}: user is {c.get('user')!r}, expected 10001")
    if c.get("readonlyRootFilesystem") is not True:
        bad.append(f"{c['name']}: readonlyRootFilesystem is not true")
    for e in c.get("environment") or []:
        if re.search(r"TOKEN|SECRET|PASSWORD|KEY", e["name"], re.I):
            bad.append(f"{c['name']}: environment variable {e['name']} looks like a secret")
    drop = ((c.get("caps") or {}).get("drop")) or []
    if "ALL" not in drop:
        bad.append(f"{c['name']}: capabilities not dropped (drop={drop})")
print("\n".join(bad) or "ok")
sys.exit(1 if bad else 0)
PY
  done
  if command -v docker >/dev/null && [ -n "${LOCAL_IMAGES:-}" ]; then
    : > "$OUT/03-docker-uid.txt"
    for img in $LOCAL_IMAGES; do
      echo "== $img" >> "$OUT/03-docker-uid.txt"
      docker run --rm --entrypoint id "$img" 2>&1 | redact >> "$OUT/03-docker-uid.txt"
    done
    grep -q 'uid=10001' "$OUT/03-docker-uid.txt" || { echo "  no uid=10001 in 03-docker-uid.txt"; failed=1; }
  else
    echo "  (docker uid check skipped: set LOCAL_IMAGES and start Docker)"
  fi
  if [ $failed -eq 0 ]; then record 3 ok "all containers non-root, read-only, caps dropped, no secret-like environment variables"
  else record 3 fail "see 03-taskdef-*.json and the lines above"; fi
}

# ------------------------------------------------------------------ check 4
check4() {
  echo "Check 4: not reachable from the internet"
  need VPC_ID ALB_NAME || { record 4 skip "config missing"; return; }
  local failed=0
  save 04-alb-scheme.txt aws elbv2 describe-load-balancers --names "$ALB_NAME" \
    --query 'LoadBalancers[].{name:LoadBalancerName,scheme:Scheme,dns:DNSName}' --output json || failed=1
  grep -q '"scheme": "internal"' "$OUT/04-alb-scheme.txt" || { echo "  ALB scheme is not internal"; failed=1; }
  local dns; dns="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["dns"])' "$OUT/04-alb-scheme.txt" 2>/dev/null)"
  if [ -n "$dns" ]; then
    # from your laptop this must fail (timeout or no route); a success means it is reachable
    if curl -sS -m 10 "http://$dns/health" > "$OUT/04-curl-timeout.txt" 2>&1; then
      echo "  curl SUCCEEDED from outside: the ALB is reachable" >> "$OUT/04-curl-timeout.txt"; failed=1
    fi
  else
    echo "no ALB DNS name found" > "$OUT/04-curl-timeout.txt"; failed=1
  fi
  save 04-sg-rules.txt aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[].{id:GroupId,inbound:IpPermissions[].{from:FromPort,to:ToPort,v4:IpRanges[].CidrIp,v6:Ipv6Ranges[].CidrIpv6}}' || failed=1
  grep -qE '0\.0\.0\.0/0|::/0' "$OUT/04-sg-rules.txt" && { echo "  an inbound rule is open to the world"; failed=1; }
  save 04-no-igw.txt aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$VPC_ID" \
    --query 'InternetGateways[].InternetGatewayId' --output json || failed=1
  [ "$(tr -d '[:space:]' < "$OUT/04-no-igw.txt")" = "[]" ] || { echo "  an internet gateway is attached"; failed=1; }
  if [ -n "${PROBE_LAMBDA:-}" ]; then
    python3 "$HERE/prove_isolation.py" --vpc-id "$VPC_ID" --alb-name "$ALB_NAME" --probe-lambda "$PROBE_LAMBDA" 2>&1 \
      | redact > "$OUT/04-lambda-call.txt"
    [ "${PIPESTATUS[0]}" -eq 0 ] || { echo "  prove_isolation.py did not pass, see 04-lambda-call.txt"; failed=1; }
  else
    echo "  (in-VPC call skipped: set PROBE_LAMBDA once the probe function exists)"
  fi
  if [ $failed -eq 0 ]; then record 4 ok "internal ALB, outside call fails, nothing open to the world, no internet gateway"
  else record 4 fail "see 04-*.txt"; fi
}

# ------------------------------------------------------------------ check 6
check6() {
  echo "Check 6: dashboard"
  need DASHBOARD || { record 6 skip "config missing"; return; }
  local failed=0
  aws cloudwatch get-dashboard --dashboard-name "$DASHBOARD" --query DashboardBody --output text 2>&1 \
    | redact > "$OUT/06-dashboard.json" || failed=1
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$OUT/06-dashboard.json" 2>/dev/null \
    || { echo "  06-dashboard.json is not valid JSON"; failed=1; }
  save 06-metrics.txt aws cloudwatch list-metrics --namespace "${METRIC_NAMESPACE:-SecureCare/Scanner}" \
    --query 'Metrics[].MetricName' --output json || failed=1
  grep -q 'ScansInfected' "$OUT/06-metrics.txt" || echo "  note: ScansInfected has no data yet (run check 1 first)"
  if [ $failed -eq 0 ]; then record 6 ok "dashboard saved (06-dashboard-live.png is still a screenshot you take, last hour)"
  else record 6 fail "see 06-dashboard.json"; fi
}

# ------------------------------------------------------------------ check 7
check7() {
  echo "Check 7: nothing costly left running"
  need CLUSTER VPC_ID || { record 7 skip "config missing"; return; }
  : > "$OUT/07-nothing-running.txt"
  local lines=0 out
  section() { # section <title> <command...>: appends output; counts non-empty results
    local title="$1"; shift
    out="$("$@" 2>&1)"
    { echo "== $title"; echo "${out:-<none>}"; } | redact >> "$OUT/07-nothing-running.txt"
    [ -n "$out" ] && [ "$out" != "[]" ] && lines=$((lines + 1))
    return 0
  }
  section "running ECS tasks in $CLUSTER" aws ecs list-tasks --cluster "$CLUSTER" --desired-status RUNNING \
    --query 'taskArns' --output text
  section "load balancers named securecare*" aws elbv2 describe-load-balancers \
    --query 'LoadBalancers[?starts_with(LoadBalancerName, `securecare`)].LoadBalancerName' --output text
  section "interface VPC endpoints in $VPC_ID" aws ec2 describe-vpc-endpoints \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=vpc-endpoint-type,Values=Interface" \
    --query 'VpcEndpoints[?State!=`deleted`].ServiceName' --output text
  section "NAT gateways in $VPC_ID" aws ec2 describe-nat-gateways \
    --filter "Name=vpc-id,Values=$VPC_ID" "Name=state,Values=available,pending" \
    --query 'NatGateways[].NatGatewayId' --output text
  echo "(paylogistics-ecs-cluster is not ours and is not checked)" >> "$OUT/07-nothing-running.txt"
  if [ $lines -eq 0 ]; then record 7 ok "every list is empty (07-cost-explorer.png is still a screenshot; run again tomorrow morning)"
  else record 7 fail "$lines list(s) not empty in 07-nothing-running.txt: destroy them now"; fi
}

# ------------------------------------------------------------------ main
if [ "${1:-}" = "--list" ]; then list_checks; exit 0; fi
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
else
  die "no $ENV_FILE found; copy scripts/gate3.env.example to $ENV_FILE and fill it in"
fi
[ "${REGION_CHECK:-}" = "eu-west-1" ] || die "REGION_CHECK in $ENV_FILE must be eu-west-1 (it guards against the wrong region)"
command -v aws >/dev/null || die "aws CLI not found"
aws sts get-caller-identity >/dev/null 2>&1 || die "no valid AWS login; run aws sso login or your usual login first"
mkdir -p "$OUT"

CHECKS=("$@")
[ ${#CHECKS[@]} -gt 0 ] || CHECKS=(2 3 4 6 7)
for c in "${CHECKS[@]}"; do
  case "$c" in 1|2|3|4|6|7) "check$c" ;; *) die "unknown check '$c' (use --list)" ;; esac
done

{
  echo "Gate 3 runner summary, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s' "$SUMMARY"
  echo
  echo "Still by hand: 01-alarm-in-alarm.png, 02-gate-fail.png, 02-gate-pass.png, 05 (README link + rebuild date + job name),"
  echo "06-dashboard-live.png, 07-cost-explorer.png."
} | tee "$OUT/00-summary.txt"

[ $FAIL -eq 0 ]
