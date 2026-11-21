# CloudFront-scope WAF metrics exist only in us-east-1, and an alarm can notify only an SNS
# topic in its own region, so this module creates both in us-east-1 through the aliased provider.
# CloudFront web ACL metrics have no Region dimension.

resource "aws_sns_topic" "waf" {
  provider = aws.us_east_1
  name     = "${var.name}-waf-alarms"
  tags     = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  provider  = aws.us_east_1
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.waf.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "blocked" {
  provider            = aws.us_east_1
  alarm_name          = "${var.name}-waf-blocked-requests"
  alarm_description   = "The web firewall blocked many requests in 5 minutes. Check the WAF logs for the source and the rule."
  namespace           = "AWS/WAFV2"
  metric_name         = "BlockedRequests"
  dimensions          = { WebACL = var.web_acl_name, Rule = "ALL" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.blocked_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.waf.arn]
  tags                = var.tags
}
