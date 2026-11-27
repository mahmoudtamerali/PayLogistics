variable "name" {
  type    = string
  default = "securecare"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "image" {
  description = "Scanner image URI in ECR (T23), built with --platform linux/amd64."
  type        = string
}

variable "cluster_arn" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "scanner_security_group_id" {
  description = "From the network module: no inbound, outbound only to S3 and the interface endpoints."
  type        = string
}

variable "cpu" {
  description = "ClamAV runtime sizing: 512 (0.5 vCPU) with 2048 MiB verified on AWS (~1.0 GB steady-state RAM, <1% idle CPU)."
  type        = number
  default     = 512
}

variable "memory" {
  type    = number
  default = 2048
}

variable "max_bytes" {
  description = "Largest file scanned; bigger files are quarantined unscanned. Agree the upload limit with the API owner."
  type        = number
  default     = 26214400
}

variable "deploy" {
  description = "false (default): no running scanner. true: create the ECS service (billed per hour). Short tests only."
  type        = bool
  default     = false
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "cors_allowed_origins" {
  description = "Website origins allowed to upload with a pre-signed URL (the CloudFront domain). Empty = no CORS rule."
  type        = list(string)
  default     = ["https://outship.me"]
}

variable "files_verdict_lambda_arn" {
  description = "Optional ARN of the securecare-files Lambda to notify on ObjectCreated events in clean/quarantine buckets."
  type        = string
  default     = null
}

variable "quarantine_retention_days" {
  type    = number
  default = 30
}

variable "force_destroy_buckets" {
  description = "true lets terraform destroy delete non-empty buckets (project teardown). false in real use."
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "Customer-managed key for the buckets. null = S3-managed keys (SSE-S3). The queue always uses SQS-managed encryption so S3 can notify it."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "tags" {
  type    = map(string)
  default = {}
}
