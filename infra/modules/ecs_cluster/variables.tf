variable "name" {
  description = "Cluster name."
  type        = string
  default     = "securecare"
}

variable "container_insights" {
  description = "Container Insights bills for its extra metrics and logs. Keep false until a demo needs it."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
