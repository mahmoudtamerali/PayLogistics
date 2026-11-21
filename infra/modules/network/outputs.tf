output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  description = "Two private subnets, one per availability zone."
  value       = aws_subnet.private[*].id
}

output "route_table_id" {
  value = aws_route_table.private.id
}

output "security_group_ids" {
  description = "One security group per service."
  value = {
    lambda    = aws_security_group.lambda.id
    alb       = aws_security_group.alb.id
    records   = aws_security_group.records.id
    scanner   = aws_security_group.scanner.id
    endpoints = aws_security_group.endpoints.id
  }
}

output "interface_endpoints_enabled" {
  description = "True while the hourly-billed interface endpoints exist. Should be false when not testing."
  value       = var.create_interface_endpoints
}
