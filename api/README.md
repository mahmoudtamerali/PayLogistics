# SecureCare API & Lambda Functions

- `openapi.yaml`: OpenAPI 3.0.1 specification exported from the deployed API Gateway REST API (`dev` stage, Cognito User Pool authorizer, CORS configured for `https://outship.me`).
- `lambdas/`: Synchronized Python package deployed across all 9 `securecare-*` AWS Lambda functions:
  - `securecare-appointments` (`handlers.appointments.handler`)
  - `securecare-assignments` (`handlers.assignments.handler`)
  - `securecare-audit` (`handlers.audit.handler`)
  - `securecare-cognito-sync` (`handlers.cognito_sync.handler`)
  - `securecare-erase` (`handlers.erase.handler`)
  - `securecare-export` (`handlers.export.handler`)
  - `securecare-files` (`handlers.files.handler` — API + S3 `ObjectCreated:*` scan-verdict DynamoDB bridge)
  - `securecare-patients` (`handlers.patients.handler`)
  - `securecare-visit-notes` (`handlers.visit_notes.handler`)

## Required Lambda Environment & IAM Notes (Managed Outside `infra/`)
- `securecare-files` requires `QUARANTINE_BUCKET` (alongside `UPLOADS_BUCKET`, `CLEAN_BUCKET`, `FILES_TABLE`, `PATIENTS_TABLE`, `KMS_KEY_ARN`) and `s3.amazonaws.com` invoke permissions for the `clean` and `quarantine` buckets.
- `securecare-files-role` requires `dynamodb:BatchGetItem` on `securecare-patients` (for `patient_name` hydration).
- `securecare-visit-notes-role` requires `dynamodb:BatchGetItem` / `dynamodb:GetItem` on `securecare-patients` and `cognito-idp:AdminGetUser` on the Cognito User Pool (for `patient_name` and `clinician_name` hydration).
