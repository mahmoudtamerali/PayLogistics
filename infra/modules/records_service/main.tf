data "aws_caller_identity" "current" {}

locals {
  notes_table       = "${var.name}-visit-notes"
  audit_table       = "${var.name}-audit"
  assignments_table = "${var.name}-assignments"
  log_group_name    = "/${var.name}/records"
  tables = {
    notes       = { name = local.notes_table, hash = "patient_id", range = "note_id" }
    audit       = { name = local.audit_table, hash = "patient_id", range = "event_id" }
    assignments = { name = local.assignments_table, hash = "clinician_id", range = "patient_id" }
  }

  # X-Ray (T62): a daemon container next to the service, reached on localhost (awsvpc shares it). Non-essential:
  # if it stops, the service keeps running and only tracing is lost. Same hardening as the other container.
  xray_environment = var.xray_enabled ? {
    XRAY_ENABLED            = "true"
    AWS_XRAY_DAEMON_ADDRESS = "127.0.0.1:2000"
  } : {}

  xray_container = var.xray_enabled ? [
    {
      name              = "xray-daemon"
      image             = var.xray_daemon_image
      essential         = false
      cpu               = 32
      memoryReservation = 128

      user                   = "10001:10001"
      readonlyRootFilesystem = true
      linuxParameters = {
        capabilities = { add = [], drop = ["ALL"] }
      }

      portMappings   = [{ containerPort = 2000, hostPort = 2000, protocol = "udp" }]
      mountPoints    = []
      systemControls = []
      volumesFrom    = []
      environment    = [{ name = "AWS_REGION", value = var.region }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = "/${var.name}/records"
          awslogs-region        = var.region
          awslogs-stream-prefix = "xray-daemon"
        }
      }
    }
  ] : []

  # Secret rotation (T105): the previous secret version, only when asked for.
  previous_token_secret = var.inject_previous_token ? [
    { name = "INTERNAL_TOKEN_PREVIOUS", valueFrom = "${aws_secretsmanager_secret.internal_token.arn}::AWSPREVIOUS:" }
  ] : []
}

# ---------------------------------------------------------------- tables
# On-demand billing: costs almost nothing while idle. Point-in-time recovery is on
# because these hold records.
resource "aws_dynamodb_table" "this" {
  for_each = local.tables

  name                        = each.value.name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = each.value.hash
  range_key                   = each.value.range
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = each.value.hash
    type = "S"
  }

  attribute {
    name = each.value.range
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  # Always encrypted at rest. With no key given DynamoDB uses its own AWS-owned key (free);
  # with a key given it uses that customer-managed key.
  dynamic "server_side_encryption" {
    for_each = var.kms_key_arn == null ? [] : [1]
    content {
      enabled     = true
      kms_key_arn = var.kms_key_arn
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------- secret container
# Terraform creates only the empty secret. The value (the token the API and this service
# share) is set outside Terraform so it never lands in the state file:
#   aws secretsmanager put-secret-value --secret-id <name> --secret-string "$(openssl rand -hex 32)"
resource "aws_secretsmanager_secret" "internal_token" {
  name                    = "${var.name}/records/internal-token"
  description             = "Shared token between the API Lambda and the records service (T40, rotated in T105)."
  recovery_window_in_days = 0 # lets teardown delete it at once; use 7 or more in real use
  tags                    = var.tags
}

# ---------------------------------------------------------------- logs
resource "aws_cloudwatch_log_group" "records" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# ---------------------------------------------------------------- IAM
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
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# Execution role: used by ECS itself to pull the image, write logs and read the secret.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-records-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.internal_token.arn]
  }
}

resource "aws_iam_role_policy" "execution_secret" {
  name   = "read-internal-token"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secret.json
}

# Task role: what the application code itself may do. Exactly the calls the code makes.
resource "aws_iam_role" "task" {
  name               = "${var.name}-records-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "Notes"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.this["notes"].arn]
  }

  statement {
    sid       = "AuditWriteOnly"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.this["audit"].arn]
  }

  statement {
    sid       = "AssignmentsReadOnly"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.this["assignments"].arn]
  }

  # /health/ready calls DescribeTable on all three tables.
  statement {
    sid       = "ReadinessCheck"
    actions   = ["dynamodb:DescribeTable"]
    resources = [for t in aws_dynamodb_table.this : t.arn]
  }

  # X-Ray accepts no resource-level restriction for these actions, so "*" is the only valid value.
  dynamic "statement" {
    for_each = var.xray_enabled ? [1] : []
    content {
      sid = "XRayWrite"
      actions = [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords",
        "xray:GetSamplingRules",
        "xray:GetSamplingTargets",
        "xray:GetSamplingStatisticSummaries",
      ]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [1]
    content {
      sid       = "UseTableKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      resources = [var.kms_key_arn]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "records-data-access"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------------------------------------------------------------- task definition
resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name}-records"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # build the image with --platform linux/amd64
  }

  container_definitions = jsonencode(concat([
    {
      name      = "records"
      image     = var.image
      essential = true

      user                   = "10001:10001"
      readonlyRootFilesystem = true
      linuxParameters = {
        capabilities = { add = [], drop = ["ALL"] }
      }

      # The empty lists and hostPort below are what AWS stores anyway. Writing them out keeps the stored
      # task definition identical to this one, so terraform does not plan a replacement on every run.
      portMappings   = [{ containerPort = var.container_port, hostPort = var.container_port, protocol = "tcp" }]
      mountPoints    = []
      systemControls = []
      volumesFrom    = []

      environment = [for k, v in merge({
        AWS_REGION        = var.region
        RECORDS_TABLE     = local.notes_table
        AUDIT_TABLE       = local.audit_table
        ASSIGNMENTS_TABLE = local.assignments_table
      }, local.xray_environment, var.extra_environment) : { name = k, value = v }]

      # Secrets come from Secrets Manager, never from the plain environment list.
      secrets = concat(
        [{ name = "INTERNAL_TOKEN", valueFrom = aws_secretsmanager_secret.internal_token.arn }],
        local.previous_token_secret,
      )

      healthCheck = {
        command = [
          "CMD", "python", "-c",
          "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.container_port}/health', timeout=3).status == 200 else 1)"
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.records.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "records"
        }
      }
    }
  ], local.xray_container))

  lifecycle {
    precondition {
      condition     = !var.xray_enabled || var.xray_daemon_image != ""
      error_message = "xray_enabled needs xray_daemon_image (an image in our own registry). Run scripts/mirror_xray_daemon.sh first."
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------- load balancer + service (billed hourly)
# Internal only: no public address. HTTP inside the VPC is an open point (see README).
resource "aws_lb" "this" {
  count = var.deploy ? 1 : 0

  name               = "${var.name}-records"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.private_subnet_ids

  drop_invalid_header_fields = true
  tags                       = var.tags
}

resource "aws_lb_target_group" "this" {
  count = var.deploy ? 1 : 0

  name        = "${var.name}-records"
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip" # required for Fargate
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
  }

  tags = var.tags
}

resource "aws_lb_listener" "http" {
  count = var.deploy ? 1 : 0

  load_balancer_arn = aws_lb.this[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[0].arn
  }
}

resource "aws_ecs_service" "this" {
  count = var.deploy ? 1 : 0

  name            = "${var.name}-records"
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
    security_groups  = [var.records_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this[0].arn
    container_name   = "records"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.http]
  tags       = var.tags
}
