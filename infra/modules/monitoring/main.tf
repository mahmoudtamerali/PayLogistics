locals {
  have_records = var.services_deployed
  have_scanner = var.services_deployed
  have_alb     = var.services_deployed
  actions      = [aws_sns_topic.alarms.arn]
}

# ---------------------------------------------------------------- notifications
# Not encrypted with the AWS-managed key on purpose: CloudWatch cannot publish to a topic
# that uses it. The messages contain alarm names only, no record data.
resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ---------------------------------------------------------------- scanner alarms
# Metrics come from the scanner's own log lines (Embedded Metric Format), namespace SecureCare/Scanner.
resource "aws_cloudwatch_metric_alarm" "scan_infected" {
  alarm_name          = "${var.name}-scan-infected-file"
  alarm_description   = "A scan found malware and the file went to quarantine. Review the quarantine bucket and who uploaded it."
  namespace           = "SecureCare/Scanner"
  metric_name         = "ScansInfected"
  dimensions          = { Service = "scanner" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "scan_failed" {
  alarm_name          = "${var.name}-scan-failed"
  alarm_description   = "The scanner could not reach a verdict (clamd down, S3 or queue error). Files stay in uploads and are retried."
  namespace           = "SecureCare/Scanner"
  metric_name         = "ScansFailed"
  dimensions          = { Service = "scanner" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "scan_backlog" {
  alarm_name          = "${var.name}-scan-backlog"
  alarm_description   = "Uploads are waiting too long for a scan. Is the scanner task running?"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = var.scan_queue_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.scan_queue_max_age_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "scan_dlq" {
  alarm_name          = "${var.name}-scan-dead-letter"
  alarm_description   = "A scan message failed three times and is in the dead-letter queue. The file is still in uploads."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.scan_dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

# ---------------------------------------------------------------- records service alarms
resource "aws_cloudwatch_metric_alarm" "audit_write_failures" {
  alarm_name          = "${var.name}-audit-write-failed"
  alarm_description   = "The records service could not write an audit entry, so it refused the action. Check the audit table and the task role."
  namespace           = "SecureCare/Records"
  metric_name         = "AuditWriteFailures"
  dimensions          = { Service = "records-service" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "denied_requests" {
  alarm_name          = "${var.name}-denied-requests-spike"
  alarm_description   = "Many denied or unauthenticated requests to the records service in 5 minutes. Look at the request ids in the logs."
  namespace           = "SecureCare/Records"
  metric_name         = "DeniedRequests"
  dimensions          = { Service = "records-service" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.denied_requests_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "records_aws_errors" {
  alarm_name          = "${var.name}-records-dependency-errors"
  alarm_description   = "The records service got errors from DynamoDB (throttling, permissions or outage)."
  namespace           = "SecureCare/Records"
  metric_name         = "AwsErrors"
  dimensions          = { Service = "records-service" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "records_memory" {
  count               = local.have_records ? 1 : 0
  alarm_name          = "${var.name}-records-memory-high"
  alarm_description   = "Records service memory above 85 percent for 10 minutes."
  namespace           = "AWS/ECS"
  metric_name         = "MemoryUtilization"
  dimensions          = { ClusterName = var.cluster_name, ServiceName = var.records_service_name }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "scanner_memory" {
  count               = local.have_scanner ? 1 : 0
  alarm_name          = "${var.name}-scanner-memory-high"
  alarm_description   = "Scanner memory above 85 percent. ClamAV is memory hungry; raise the task memory before it is killed."
  namespace           = "AWS/ECS"
  metric_name         = "MemoryUtilization"
  dimensions          = { ClusterName = var.cluster_name, ServiceName = var.scanner_service_name }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  count               = local.have_alb ? 1 : 0
  alarm_name          = "${var.name}-records-alb-5xx"
  alarm_description   = "The records service returned server errors through the load balancer."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.target_group_arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy" {
  count               = local.have_alb ? 1 : 0
  alarm_name          = "${var.name}-records-unhealthy-targets"
  alarm_description   = "The load balancer sees an unhealthy records task."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.target_group_arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  tags                = var.tags
}

# ---------------------------------------------------------------- dashboard
resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 2
        properties = { markdown = "# SecureCare overview\nUploads and scanning on the left, records service on the right. Red alarm bars mean see the alarm description." }
      },
      {
        type = "metric", x = 0, y = 2, width = 12, height = 6
        properties = {
          title  = "Scan results (5 min)"
          region = var.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["SecureCare/Scanner", "ScansClean", "Service", "scanner"],
            [".", "ScansInfected", ".", "."],
            [".", "ScansTooLarge", ".", "."],
            [".", "ScansFailed", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 2, width = 12, height = 6
        properties = {
          title  = "Records service activity (5 min)"
          region = var.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["SecureCare/Records", "NotesCreated", "Service", "records-service"],
            [".", "DeniedRequests", ".", "."],
            [".", "ValidationFailures", ".", "."],
            [".", "AuditWriteFailures", ".", "."],
            [".", "AwsErrors", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 8, width = 12, height = 6
        properties = {
          title  = "Scan queue"
          region = var.region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.scan_queue_name, { stat = "Maximum" }],
            [".", "ApproximateAgeOfOldestMessage", ".", ".", { stat = "Maximum", yAxis = "right" }],
            [".", "ApproximateNumberOfMessagesVisible", "QueueName", var.scan_dlq_name, { stat = "Maximum", label = "dead-letter queue" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 8, width = 12, height = 6
        properties = {
          title  = "Scan duration (ms)"
          region = var.region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["SecureCare/Scanner", "ScanDurationMs", "Service", "scanner", { stat = "Average" }],
            ["...", { stat = "p95" }],
          ]
        }
      },
      {
        type = "alarm", x = 0, y = 14, width = 24, height = 6
        properties = {
          title = "Alarm status"
          alarms = concat(
            [
              aws_cloudwatch_metric_alarm.scan_infected.arn,
              aws_cloudwatch_metric_alarm.scan_failed.arn,
              aws_cloudwatch_metric_alarm.scan_backlog.arn,
              aws_cloudwatch_metric_alarm.scan_dlq.arn,
              aws_cloudwatch_metric_alarm.audit_write_failures.arn,
              aws_cloudwatch_metric_alarm.denied_requests.arn,
              aws_cloudwatch_metric_alarm.records_aws_errors.arn,
            ],
            aws_cloudwatch_metric_alarm.records_memory[*].arn,
            aws_cloudwatch_metric_alarm.scanner_memory[*].arn,
            aws_cloudwatch_metric_alarm.alb_5xx[*].arn,
            aws_cloudwatch_metric_alarm.alb_unhealthy[*].arn,
          )
        }
      },
    ]
  })
}
