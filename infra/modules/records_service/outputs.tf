output "table_names" {
  value = { for k, t in aws_dynamodb_table.this : k => t.name }
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.this.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.records.name
}

output "internal_token_secret_arn" {
  description = "Put the value here with put-secret-value; share the secret name with the API owner."
  value       = aws_secretsmanager_secret.internal_token.arn
}

output "service_name" {
  description = "Empty string while deploy is false."
  value       = try(aws_ecs_service.this[0].name, "")
}

output "alb_dns_name" {
  description = "Internal DNS name for the API Lambda to call. Empty string while deploy is false."
  value       = try(aws_lb.this[0].dns_name, "")
}

output "alb_arn_suffix" {
  value = try(aws_lb.this[0].arn_suffix, "")
}

output "target_group_arn_suffix" {
  value = try(aws_lb_target_group.this[0].arn_suffix, "")
}
