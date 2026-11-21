variable "name" {
  type    = string
  default = "securecare"
}

variable "web_acl_name" {
  description = "Name (not id) of the CloudFront-scope web ACL created by the WAF owner."
  type        = string
}

variable "blocked_threshold" {
  description = "Blocked requests per 5 minutes that count as an attack worth looking at."
  type        = number
  default     = 100
}

variable "alarm_email" {
  type    = string
  default = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
