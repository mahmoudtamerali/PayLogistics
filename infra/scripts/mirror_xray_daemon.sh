#!/usr/bin/env bash
# Copy the AWS X-Ray daemon image into OUR ECR repository (securecare-xray-daemon).
#
# Why: the VPC has no internet, so an ECS task cannot pull the daemon from the public registry.
# Run this once on a machine with internet, docker and an AWS login, after `terraform apply` has created
# the repository. It scans the image with the image gate first and pushes only if the gate passes.
#
#   bash scripts/mirror_xray_daemon.sh
#
# Then turn tracing on for a test session:
#   terraform apply -var deploy=true -var interface_endpoints=true -var xray=true
# and switch it off again afterwards (the endpoint bills by the hour).
set -euo pipefail

REGION="eu-west-1"
SOURCE="${XRAY_DAEMON_SOURCE:-public.ecr.aws/xray/aws-xray-daemon:latest}"
HERE="$(cd "$(dirname "$0")" && pwd)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
TARGET="${REGISTRY}/securecare-xray-daemon:mirror"

if ! aws ecr describe-repositories --repository-names securecare-xray-daemon --region "$REGION" >/dev/null 2>&1; then
  echo "Repository securecare-xray-daemon does not exist yet. Run terraform apply first." >&2
  exit 2
fi

docker pull --platform linux/amd64 "$SOURCE"
echo "Source digest (note it down): $(docker inspect --format '{{index .RepoDigests 0}}' "$SOURCE")"

echo "Image gate:"
python3 "$HERE/image_gate.py" --image "$SOURCE" || { echo "Blocked by the image gate; nothing was pushed." >&2; exit 1; }

docker tag "$SOURCE" "$TARGET"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker push "$TARGET"
echo "Pushed. First thing to check in the test session: the xray-daemon container stays RUNNING"
echo "(it runs as uid 10001 with a read-only filesystem, which has not been tried yet)."
