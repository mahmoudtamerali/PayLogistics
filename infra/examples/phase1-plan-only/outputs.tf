# Values the Gate 3 runner (scripts/gate3.env.example) and the test commands need.

output "ecr_registry" {
  value = local.registry
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "security_group_ids" {
  value = module.network.security_group_ids
}

output "bucket_names" {
  value = module.scanner.bucket_names
}

output "records_secret_arn" {
  value = module.records.internal_token_secret_arn
}

output "alb_dns_name" {
  description = "Empty while deploy is false."
  value       = module.records.alb_dns_name
}

output "alarm_topic_arn" {
  value = module.monitoring.alarm_topic_arn
}

output "probe_lambda" {
  description = "Empty while deploy is false."
  value       = try(aws_lambda_function.probe[0].function_name, "")
}
