output "topic_arn" {
  value = aws_sns_topic.waf.arn
}

output "alarm_name" {
  value = aws_cloudwatch_metric_alarm.blocked.alarm_name
}
