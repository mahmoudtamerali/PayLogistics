# T11: network for SecureCare.
#
# Decisions (see docs/decisions.md):
#  - One VPC, private subnets only, in two availability zones.
#  - No internet gateway and no NAT gateway. Nothing in the VPC can reach the
#    internet. AWS services are reached through VPC endpoints instead.
#  - Free gateway endpoints for S3 and DynamoDB are always created.
#  - Interface endpoints (billed by the hour) are created only when
#    create_interface_endpoints = true (Phase 3, T52), in one zone.
#  - One security group per service, so rules can say "only that group".

locals {
  tags = merge(var.tags, { ManagedBy = "terraform" })

  # Services reached through interface endpoints (T52).
  interface_services = concat(
    [
      "ecr.api",
      "ecr.dkr",
      "logs",
      "sqs",
      "kms",
    ],
    var.extra_interface_endpoint_services,
  )
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # needed for interface endpoint private DNS

  tags = merge(local.tags, { Name = "${var.name}-vpc" })
}

# Closing the default security group means nothing can use it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-default-closed" })
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.private_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false

  tags = merge(local.tags, { Name = "${var.name}-private-${var.availability_zones[count.index]}" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  # Deliberately no 0.0.0.0/0 route: there is no internet or NAT path.
  tags = merge(local.tags, { Name = "${var.name}-private" })
}

resource "aws_route_table_association" "private" {
  count = 2

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------- Gateway endpoints (free) ----------

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(local.tags, { Name = "${var.name}-s3-gateway" })
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(local.tags, { Name = "${var.name}-dynamodb-gateway" })
}

# ---------- Security groups: one per service ----------
# Rules that reference another group are separate resources, so the groups do
# not depend on each other in a cycle.

resource "aws_security_group" "lambda" {
  name        = "${var.name}-lambda"
  description = "API Lambda functions that call the records service"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-lambda-sg" })
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-internal-alb"
  description = "Internal load balancer in front of the records service"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-internal-alb-sg" })
}

resource "aws_security_group" "records" {
  name        = "${var.name}-records-service"
  description = "Records service tasks"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-records-sg" })
}

resource "aws_security_group" "scanner" {
  name        = "${var.name}-scanner"
  description = "File scanner tasks (no inbound traffic at all)"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-scanner-sg" })
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name}-vpc-endpoints"
  description = "Interface endpoints, reachable only from our services"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-endpoints-sg" })
}

# Lambda -> internal load balancer
resource "aws_vpc_security_group_egress_rule" "lambda_to_alb" {
  security_group_id            = aws_security_group.lambda.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "Lambda to internal load balancer"
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_lambda" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.lambda.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "Only the Lambda security group may call the load balancer"
}

# Internal load balancer -> records service
resource "aws_vpc_security_group_egress_rule" "alb_to_records" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.records.id
  ip_protocol                  = "tcp"
  from_port                    = var.records_port
  to_port                      = var.records_port
  description                  = "Load balancer to records service"
}

resource "aws_vpc_security_group_ingress_rule" "records_from_alb" {
  security_group_id            = aws_security_group.records.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.records_port
  to_port                      = var.records_port
  description                  = "Only the load balancer may reach the records service"
}

# Records service and scanner -> interface endpoints (HTTPS)
resource "aws_vpc_security_group_egress_rule" "records_to_endpoints" {
  security_group_id            = aws_security_group.records.id
  referenced_security_group_id = aws_security_group.endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Records service to AWS service endpoints"
}

resource "aws_vpc_security_group_egress_rule" "scanner_to_endpoints" {
  security_group_id            = aws_security_group.scanner.id
  referenced_security_group_id = aws_security_group.endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Scanner to AWS service endpoints"
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_records" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.records.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Records service to endpoints"
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_scanner" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.scanner.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Scanner to endpoints"
}

# Tasks also reach S3 and DynamoDB through the gateway endpoints. Those are
# addressed by AWS prefix list, not by security group, so allow HTTPS to them.
data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${var.region}.s3"
}

data "aws_prefix_list" "dynamodb" {
  name = "com.amazonaws.${var.region}.dynamodb"
}

resource "aws_vpc_security_group_egress_rule" "records_to_s3" {
  security_group_id = aws_security_group.records.id
  prefix_list_id    = data.aws_prefix_list.s3.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Records service to S3 gateway endpoint (image layers)"
}

resource "aws_vpc_security_group_egress_rule" "records_to_dynamodb" {
  security_group_id = aws_security_group.records.id
  prefix_list_id    = data.aws_prefix_list.dynamodb.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Records service to DynamoDB gateway endpoint"
}

resource "aws_vpc_security_group_egress_rule" "scanner_to_s3" {
  security_group_id = aws_security_group.scanner.id
  prefix_list_id    = data.aws_prefix_list.s3.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Scanner to S3 gateway endpoint (uploads, quarantine, image layers)"
}

# ---------- Interface endpoints (billed hourly; off until Phase 3) ----------
# One zone only, to halve the cost. Turn create_interface_endpoints off again
# when you are not testing.

resource "aws_vpc_endpoint" "interface" {
  for_each = var.create_interface_endpoints ? toset(local.interface_services) : toset([])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private[0].id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.name}-${replace(each.value, ".", "-")}-endpoint" })
}
