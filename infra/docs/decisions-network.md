# Decision: network (T11)

Date: 2026-11-10. Owners: Lead and Container.

- **Region:** eu-west-1 (Ireland). Cost estimates in the project guide were written for us-east-1; eu-west-1 can be 10 to 30 percent higher. CloudFront's WAF and its certificate must still be created in us-east-1.
- **VPC:** one VPC, 10.20.0.0/16 (the account's default VPC uses 172.31.0.0/16, so ours does not overlap).
- **Subnets:** two private subnets in two availability zones (eu-west-1a, eu-west-1b). No public subnets.
- **No internet gateway and no NAT gateway.** Nothing in the VPC can reach the internet. A NAT gateway would cost about $33 a month and would give containers a route out; endpoints keep traffic on the AWS network.
- **Gateway endpoints for S3 and DynamoDB:** always created, free.
- **Interface endpoints (ECR x2, CloudWatch Logs, SQS, KMS):** created only in Phase 3 (T52), in one availability zone, and switched off after test sessions. They bill by the hour.
- **Security groups:** one per service (Lambda, internal load balancer, records service, scanner, endpoints). The records service accepts traffic only from the load balancer, and the load balancer accepts traffic only from the Lambda group. The scanner accepts no inbound traffic.
- **Records service:** Python 3.12 and FastAPI, in a non-root container, port 8080, health check on `/health`.

## Decision 2026-11-21: one VPC for the project
`securecare-vpc` (created by `modules/network`, applied from `examples/phase1-plan-only`) is the project's only VPC. The shared baseline does not create another one; it references this VPC by id. Gate 3 (checks 3, 4, 6 and 7) was run on it on 2026-11-20. Moving to a different VPC would mean changing the module inputs, destroying this one and repeating those checks.
