data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  buckets = {
    uploads    = "${var.name}-uploads-${local.account_id}"
    clean      = "${var.name}-clean-${local.account_id}"
    quarantine = "${var.name}-quarantine-${local.account_id}"
  }
}

# ---------------------------------------------------------------- buckets
resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = var.force_destroy_buckets
  tags          = merge(var.tags, { Purpose = each.key })
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced" # no ACLs
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    bucket_key_enabled       = true
    blocked_encryption_types = ["SSE-C"]
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == null ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  for_each = aws_s3_bucket.this

  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# Anything still in uploads after two days was never scanned successfully; do not keep it.
resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.this["uploads"].id

  rule {
    id     = "expire-unscanned"
    status = "Enabled"
    filter {}
    expiration {
      days = 2
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "quarantine" {
  bucket = aws_s3_bucket.this["quarantine"].id

  rule {
    id     = "expire-quarantine"
    status = "Enabled"
    filter {}
    expiration {
      days = var.quarantine_retention_days
    }
  }
}

# Browser uploads with a pre-signed URL need CORS on the uploads bucket only.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  count  = length(var.cors_allowed_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.this["uploads"].id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

# ---------------------------------------------------------------- queue
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-scan-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "scan" {
  name                       = "${var.name}-scan"
  visibility_timeout_seconds = 300 # longer than the slowest scan
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true # a customer key would need extra key-policy work for S3 to publish

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })

  tags = var.tags
}

data "aws_iam_policy_document" "queue_from_s3" {
  statement {
    sid       = "AllowUploadsBucketToNotify"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.scan.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.this["uploads"].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "scan" {
  queue_url = aws_sqs_queue.scan.id
  policy    = data.aws_iam_policy_document.queue_from_s3.json
}

resource "aws_s3_bucket_notification" "uploads" {
  bucket = aws_s3_bucket.this["uploads"].id

  queue {
    queue_arn = aws_sqs_queue.scan.arn
    events    = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_sqs_queue_policy.scan]
}

# ---------------------------------------------------------------- logs and IAM
resource "aws_cloudwatch_log_group" "scanner" {
  name              = "/${var.name}/scanner"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-scanner-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-scanner-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ReadAndRemoveUploads"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.this["uploads"].arn}/*"]
  }

  # Without ListBucket S3 answers "forbidden" instead of "not found" for a missing key,
  # and the worker could not tell "already handled" from a real permission problem.
  statement {
    sid       = "TellMissingFromForbidden"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.this["uploads"].arn]
  }

  statement {
    sid     = "WriteVerdictBuckets"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.this["clean"].arn}/*",
      "${aws_s3_bucket.this["quarantine"].arn}/*",
    ]
  }

  statement {
    sid       = "ReadQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.scan.arn]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [1]
    content {
      sid       = "UseBucketKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [var.kms_key_arn]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "scanner-data-access"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------------------------------------------------------------- task definition and service
resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name}-scanner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  # Fargate has no tmpfs. A volume with no host path is task-local scratch space; with the
  # root filesystem read-only, /tmp is the only place clamd and the worker can write.
  volume {
    name = "scratch"
  }

  container_definitions = jsonencode([
    {
      name      = "scanner"
      image     = var.image
      essential = true

      user                   = "10001:10001"
      readonlyRootFilesystem = true
      linuxParameters = {
        capabilities = { add = [], drop = ["ALL"] }
      }

      # The empty lists below are what AWS stores anyway. Writing them out keeps the stored task
      # definition identical to this one, so terraform does not plan a replacement on every run.
      mountPoints    = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
      portMappings   = []
      systemControls = []
      volumesFrom    = []

      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "UPLOADS_BUCKET", value = local.buckets.uploads },
        { name = "CLEAN_BUCKET", value = local.buckets.clean },
        { name = "QUARANTINE_BUCKET", value = local.buckets.quarantine },
        { name = "QUEUE_URL", value = aws_sqs_queue.scan.url },
        { name = "MAX_BYTES", value = tostring(var.max_bytes) },
      ]

      healthCheck = {
        command     = ["CMD", "python", "-m", "scanner.healthcheck"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 300 # clamd loads its signatures first
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.scanner.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "scanner"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_service" "this" {
  count = var.deploy ? 1 : 0

  name            = "${var.name}-scanner"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.scanner_security_group_id]
    assign_public_ip = false
  }

  tags = var.tags
}
