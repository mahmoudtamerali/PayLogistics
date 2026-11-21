# EventBridge rule -> Lambda -> SNS. Read-only: the function never changes resources.

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/build/incident.zip"
  excludes    = ["__pycache__"]
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name}-incident"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name}-incident-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "permissions" {
  statement {
    sid       = "PublishIncident"
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "incident"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.permissions.json
}

resource "aws_lambda_function" "incident" {
  function_name    = "${var.name}-incident"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      TOPIC_ARN = var.sns_topic_arn
      ENV_NAME  = var.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]
  tags       = var.tags
}

resource "aws_cloudwatch_event_rule" "alarm_to_alarm" {
  name        = "${var.name}-alarm-state-alarm"
  description = "Alarms of this project that move to ALARM"
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = [{ prefix = var.alarm_name_prefix }]
      state     = { value = ["ALARM"] }
    }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule = aws_cloudwatch_event_rule.alarm_to_alarm.name
  arn  = aws_lambda_function.incident.arn

  retry_policy {
    maximum_retry_attempts       = 2
    maximum_event_age_in_seconds = 3600
  }
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.incident.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alarm_to_alarm.arn
}
