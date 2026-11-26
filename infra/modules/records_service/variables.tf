variable "name" {
  type    = string
  default = "securecare"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "image" {
  description = "Full image URI in ECR, for example <account>.dkr.ecr.eu-west-1.amazonaws.com/securecare-records:dev (T23 creates the repository)."
  type        = string
}

variable "cluster_arn" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "vpc_id" {
  type = string
}

variable "records_security_group_id" {
  description = "From the network module: allows port 8080 from the ALB only."
  type        = string
}

variable "alb_security_group_id" {
  type = string
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "memory" {
  description = "Fargate memory in MiB."
  type        = number
  default     = 512
}

variable "deploy" {
  description = <<-EOT
    false (default): create only the free or near-free pieces (tables, roles, secret container,
    log group, task definition). true: also create the internal load balancer and the ECS service,
    which bill by the hour. Set true only for a short test (Phase 3) and destroy straight after.
  EOT
  type        = bool
  default     = false
}

variable "desired_count" {
  description = "Number of tasks when deploy is true."
  type        = number
  default     = 1
}

variable "kms_key_arn" {
  description = "Customer-managed key for DynamoDB. null = the AWS-owned default key (free)."
  type        = string
  default     = null
}

variable "log_retention_days" {
  description = "How long logs are kept. 90 days matches the Compliance retention schedule; storage is cheap at this size."
  type        = number
  default     = 90
}

variable "deletion_protection" {
  description = "Protects the tables from accidental delete. Leave false in the project so teardown works; true in real use."
  type        = bool
  default     = false
}

variable "extra_environment" {
  description = "Extra plain (non-secret) environment variables."
  type        = map(string)
  default     = {}
}

variable "xray_enabled" {
  description = <<-EOT
    false (default): no tracing, and the task definition is exactly as before. true: add the X-Ray daemon
    as a second container in the task, give the task role permission to send traces, and switch the
    service's tracing on (XRAY_ENABLED). Needs an X-Ray interface endpoint in the network module
    (extra_interface_endpoint_services includes "xray") and xray_daemon_image. Test sessions only.
  EOT
  type        = bool
  default     = false
}

variable "xray_daemon_image" {
  description = "Image URI of the X-Ray daemon in OUR registry (the VPC has no internet; run scripts/mirror_xray_daemon.sh once). Required when xray_enabled is true."
  type        = string
  default     = ""
}

variable "inject_previous_token" {
  description = <<-EOT
    Secret rotation (T105). false (default): only the current token is injected. true: also inject the
    previous version of the secret as INTERNAL_TOKEN_PREVIOUS, so callers that still hold the old token keep
    working while they switch. Turn on only AFTER the secret has had a second value put into it (the
    AWSPREVIOUS version does not exist before that and tasks would fail to start). See docs/secret-rotation.md.
  EOT
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
