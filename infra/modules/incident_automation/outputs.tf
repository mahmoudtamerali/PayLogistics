output "function_name" {
  value = aws_lambda_function.incident.function_name
}

output "rule_name" {
  value = aws_cloudwatch_event_rule.alarm_to_alarm.name
}
