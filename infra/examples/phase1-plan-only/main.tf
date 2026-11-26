# Solo composition of the whole container layer: its own VPC, ECR repositories, records service,
# scanner, monitoring and incident automation. You can apply it without the Lead's baseline.
#
#   terraform init
#   terraform validate
#   terraform plan -out=stack.plan
#   terraform apply stack.plan                       # deploy = false: no load balancer, no tasks
#
# Test sessions (see the README): turn the hourly-billed parts on with variables, test, then
# turn them off again in the same sitting.
#
#   terraform apply -var deploy=true -var interface_endpoints=true
#   terraform apply -var deploy=false -var interface_endpoints=false      # destroys only those parts
#
# In Phase 3 the Lead's baseline (T12) normally calls these same modules from the shared root.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = "eu-west-1"
}

# Needed only by the waf_alarm module (CloudFront-scope WAF metrics live in us-east-1).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

# Customer-managed key already in use for the DynamoDB tables and the scanner buckets (set up
# outside this stack). Looked up by alias so no key id/ARN is written into any file here.
data "aws_kms_alias" "data" {
  name = "alias/securecare-data"
}

variable "deploy" {
  description = "Create the load balancer and run the ECS services (these bill by the hour). Test sessions only."
  type        = bool
  default     = false
}

variable "interface_endpoints" {
  description = "Create the interface VPC endpoints (billed per hour). Needed while deploy is true."
  type        = bool
  default     = false
}

variable "xray" {
  description = <<-EOT
    X-Ray tracing for the records service (T62). false (default) changes nothing. true adds the X-Ray interface
    endpoint (billed per hour), the daemon container and the permissions. Needs the daemon image mirrored first:
    bash scripts/mirror_xray_daemon.sh. Test sessions only.
  EOT
  type        = bool
  default     = false
}

variable "inject_previous_token" {
  description = "Secret rotation (T105): also inject the previous secret version. Turn on only after the second put-secret-value. See docs/secret-rotation.md."
  type        = bool
  default     = false
}

variable "alarm_email" {
  description = "Address for alarm emails; AWS sends a confirmation mail you must click. Empty = topic only."
  type        = string
  default     = ""
}

locals {
  registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.eu-west-1.amazonaws.com"
  tags     = { Project = "SecureCare", Phase = "1", ManagedBy = "terraform" }

  # Everything that bills by the hour hangs on this one switch. Test sessions only, then back to false.
  deploy = var.deploy
}

# ECR repositories (T23). Free for the small amount of storage we use; force_delete lets terraform destroy
# remove them even when they hold images.
resource "aws_ecr_repository" "images" {
  # securecare-xray-daemon holds the mirrored X-Ray daemon image (scripts/mirror_xray_daemon.sh). The repository
  # stays even when tracing is off, so the mirrored image survives between test sessions.
  for_each = toset(["securecare-records", "securecare-scanner", "securecare-xray-daemon"])

  name                 = each.key
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

module "network" {
  source = "../../modules/network"

  create_interface_endpoints        = var.interface_endpoints
  extra_interface_endpoint_services = concat(["secretsmanager"], var.xray ? ["xray"] : [])
  tags                              = local.tags
}

module "cluster" {
  source = "../../modules/ecs_cluster"

  tags = local.tags
}

module "records" {
  source = "../../modules/records_service"

  image                     = "${local.registry}/securecare-records:dev"
  cluster_arn               = module.cluster.cluster_arn
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  records_security_group_id = module.network.security_group_ids["records"]
  alb_security_group_id     = module.network.security_group_ids["alb"]

  deploy = local.deploy

  xray_enabled          = var.xray
  xray_daemon_image     = var.xray ? "${local.registry}/securecare-xray-daemon:mirror" : ""
  inject_previous_token = var.inject_previous_token

  kms_key_arn = data.aws_kms_alias.data.target_key_arn

  tags = local.tags
}

module "scanner" {
  source = "../../modules/scanner"

  image                     = "${local.registry}/securecare-scanner:dev"
  cluster_arn               = module.cluster.cluster_arn
  private_subnet_ids        = module.network.private_subnet_ids
  scanner_security_group_id = module.network.security_group_ids["scanner"]

  deploy      = local.deploy
  kms_key_arn = data.aws_kms_alias.data.target_key_arn
  tags        = local.tags
}

module "monitoring" {
  source = "../../modules/monitoring"

  cluster_name    = module.cluster.cluster_name
  scan_queue_name = module.scanner.scan_queue_name
  scan_dlq_name   = module.scanner.dlq_name

  services_deployed       = local.deploy
  records_service_name    = module.records.service_name
  scanner_service_name    = module.scanner.service_name
  alb_arn_suffix          = module.records.alb_arn_suffix
  target_group_arn_suffix = module.records.target_group_arn_suffix

  alarm_email = var.alarm_email
  tags        = local.tags
}

# T74: alarm -> EventBridge -> Lambda -> readable incident message on the same SNS topic.
# Needs the hashicorp/archive provider, so run `terraform init` again after adding this block.
module "incident_automation" {
  source = "../../modules/incident_automation"

  sns_topic_arn = module.monitoring.alarm_topic_arn
  tags          = local.tags
}

# Throwaway in-VPC probe for the isolation proof (scripts/prove_isolation.py --probe-lambda securecare-probe).
# It lives only while deploy is true, so destroying the test session removes it too.
data "archive_file" "probe" {
  type        = "zip"
  source_file = "${path.module}/../../scripts/probe_lambda.py"
  output_path = "${path.module}/probe.zip"
}

data "aws_iam_policy_document" "probe_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "probe" {
  count = var.deploy ? 1 : 0

  name               = "securecare-probe"
  assume_role_policy = data.aws_iam_policy_document.probe_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "probe_vpc" {
  count = var.deploy ? 1 : 0

  role       = aws_iam_role.probe[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Lets the probe read the records service's internal token for the authenticated-path test only.
# The secret uses the default AWS-managed Secrets Manager key (no CMK), so no kms:Decrypt is needed here.
data "aws_iam_policy_document" "probe_secret" {
  count = var.deploy ? 1 : 0

  statement {
    sid       = "ReadInternalToken"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.records.internal_token_secret_arn]
  }
}

resource "aws_iam_role_policy" "probe_secret" {
  count = var.deploy ? 1 : 0

  name   = "securecare-probe-secret"
  role   = aws_iam_role.probe[0].name
  policy = data.aws_iam_policy_document.probe_secret[0].json
}

resource "aws_lambda_function" "probe" {
  count = var.deploy ? 1 : 0

  function_name    = "securecare-probe"
  role             = aws_iam_role.probe[0].arn
  runtime          = "python3.12"
  handler          = "probe_lambda.handler"
  filename         = data.archive_file.probe.output_path
  source_code_hash = data.archive_file.probe.output_base64sha256
  timeout          = 15

  vpc_config {
    subnet_ids         = module.network.private_subnet_ids
    security_group_ids = [module.network.security_group_ids["lambda"]]
  }

  tags = local.tags
}

# Uncomment when the WAF owner has created the CloudFront web ACL (Phase 3):
# module "waf_alarm" {
#   source    = "../../modules/waf_alarm"
#   providers = { aws.us_east_1 = aws.us_east_1 }
#
#   web_acl_name = "securecare-cloudfront-acl"
#   tags         = local.tags
# }
