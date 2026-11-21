output "bucket_names" {
  value = local.buckets
}

output "bucket_arns" {
  value = { for k, b in aws_s3_bucket.this : k => b.arn }
}

output "scan_queue_name" {
  value = aws_sqs_queue.scan.name
}

output "scan_queue_url" {
  value = aws_sqs_queue.scan.url
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.this.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.scanner.name
}

output "service_name" {
  description = "Empty string while deploy is false."
  value       = try(aws_ecs_service.this[0].name, "")
}
