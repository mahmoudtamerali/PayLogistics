# SecureCare: container and monitoring layer

Records service and ClamAV file scanner on ECS Fargate (AWS eu-west-1), plus the network, monitoring and incident automation around them.

```
infra/             Terraform modules, the applied stack (examples/phase1-plan-only), scripts, tests,
                   and evidence/ (Gate 3 results and screenshots; kept out of git, contains account details)
records-service/   FastAPI records service (port 8080, non-root, read-only filesystem)
scanner/           ClamAV worker: S3 -> SQS -> scan -> clean / quarantine
docs/              Exported project documents and the deck
```

## Cost rule
The base stack stays on (a few dollars a month). Anything that bills by the hour only exists while `deploy = true`.
Run a test session, then set it back and destroy:

```
cd infra/examples/phase1-plan-only
terraform apply -var deploy=true -var interface_endpoints=true
cd ../.. && bash scripts/gate3_runner.sh   # checks 1-7, evidence goes to infra/evidence/gate3
cd examples/phase1-plan-only
terraform apply -var deploy=false -var interface_endpoints=false
```

## Tests (offline, no AWS)
```
cd infra && python -m pytest tests
cd records-service && python -m pytest
cd scanner && python -m pytest
```

## Local files that are not in git
`terraform.tfstate`, `terraform.tfvars`, `gate3.env`, `infra/evidence/`. Keep a copy of the state somewhere off this computer.
