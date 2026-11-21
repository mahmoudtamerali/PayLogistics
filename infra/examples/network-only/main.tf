# Stand-alone way to try the network module. The Lead's baseline (T12) will
# normally call this module from the shared root instead.
#
#   terraform init
#   terraform validate
#   terraform plan
#
# Everything in this module is free while create_interface_endpoints is false
# (VPC, subnets, route table, security groups and the two gateway endpoints).

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = "eu-west-1"
}

module "network" {
  source = "../../modules/network"

  create_interface_endpoints = false # Phase 3 only (T52)
}

output "network" {
  value = {
    vpc_id             = module.network.vpc_id
    private_subnet_ids = module.network.private_subnet_ids
    security_group_ids = module.network.security_group_ids
  }
}
