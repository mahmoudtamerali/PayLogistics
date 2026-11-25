variable "enable_backup" {
  description = "AWS Backup is billed by size. Keep false on the free account until the Team Lead approves the budget."
  type        = bool
  default     = false
}

variable "dynamodb_table_arns" {
  description = "ARNs of the DynamoDB tables to back up (appointments, visit notes, ...). Ask the Lead for the real ones."
  type        = list(string)
  default     = []
}

variable "s3_bucket_arns" {
  description = "ARNs of the S3 buckets to back up (lab files). Versioning must be on for each one."
  type        = list(string)
  default     = []
}

variable "lab_files_bucket_id" {
  description = "Name of the S3 bucket that holds lab files."
  type        = string
}

variable "audit_bucket_id" {
  description = "Name of the S3 bucket that holds the CloudTrail audit logs."
  type        = string
}

# Retention periods. ALL are assumptions from the Retention Schedule sheet,
# not legal text. Change them here after the instructor reviews them.
variable "lab_files_retention_days" {
  type    = number
  default = 1825 # 5 years
}

variable "old_versions_retention_days" {
  type    = number
  default = 30
}

variable "audit_retention_days" {
  type    = number
  default = 365 # 1 year
}

variable "backup_retention_days" {
  type    = number
  default = 35 # matches the DynamoDB point-in-time window
}
