variable "name" {
  type    = string
  default = "securecare"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "alarm_email" {
  description = "Address for alarm emails. Empty = topic only. AWS sends a confirmation mail that someone must click."
  type        = string
  default     = ""
}

# Scanner
variable "scan_queue_name" {
  type = string
}

variable "scan_dlq_name" {
  type = string
}

variable "scan_queue_max_age_seconds" {
  description = "Alarm if the oldest waiting upload is older than this."
  type        = number
  default     = 900
}

# Records service
variable "cluster_name" {
  type = string
}

variable "services_deployed" {
  description = <<-EOT
    true when the records and scanner services and the load balancer exist (the modules' deploy = true).
    A plain true/false on purpose: Terraform cannot decide how many alarms to create from values that
    are only known after apply, such as the load balancer name suffix.
  EOT
  type        = bool
  default     = false
}

variable "records_service_name" {
  description = "Used only when services_deployed is true."
  type        = string
  default     = ""
}

variable "scanner_service_name" {
  type    = string
  default = ""
}

variable "alb_arn_suffix" {
  type    = string
  default = ""
}

variable "target_group_arn_suffix" {
  type    = string
  default = ""
}

variable "denied_requests_threshold" {
  description = "Denied or unauthenticated requests per 5 minutes that count as suspicious."
  type        = number
  default     = 10
}

variable "tags" {
  type    = map(string)
  default = {}
}
