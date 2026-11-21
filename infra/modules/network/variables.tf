variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "securecare"
}

variable "region" {
  description = "AWS region. Team decision: eu-west-1 (Ireland)."
  type        = string
  default     = "eu-west-1"
}

variable "vpc_cidr" {
  description = "VPC range. Not 172.31.0.0/16, which the account's default VPC already uses."
  type        = string
  default     = "10.20.0.0/16"
}

variable "availability_zones" {
  description = "Two zones for the private subnets."
  type        = list(string)
  default     = ["eu-west-1a", "eu-west-1b"]

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Use exactly two availability zones."
  }
}

variable "private_subnet_cidrs" {
  description = "One private subnet per availability zone."
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "records_port" {
  description = "Port the records service listens on (matches the Dockerfile)."
  type        = number
  default     = 8080
}

# Interface endpoints bill by the hour (about $0.01 per hour per zone each), so
# they stay off until Phase 3 (T52). Turn on only while testing, then off again.
variable "create_interface_endpoints" {
  description = "Create the interface endpoints (ECR x2, CloudWatch Logs, SQS, KMS) in ONE zone."
  type        = bool
  default     = false
}

variable "extra_interface_endpoint_services" {
  description = "Extra interface endpoint service names, e.g. [\"secretsmanager\", \"xray\"]. Tell the Lead the cost."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags added to everything."
  type        = map(string)
  default = {
    Project = "SecureCare"
    Layer   = "network"
  }
}
