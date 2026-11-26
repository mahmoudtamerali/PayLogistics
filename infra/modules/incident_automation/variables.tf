variable "name" {
  type    = string
  default = "securecare"
}

variable "sns_topic_arn" {
  description = "Topic that receives the incident message (use module.monitoring.alarm_topic_arn)."
  type        = string
}

variable "alarm_name_prefix" {
  description = "Only alarms whose name starts with this trigger the function."
  type        = string
  default     = "securecare-"
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "tags" {
  type    = map(string)
  default = {}
}
