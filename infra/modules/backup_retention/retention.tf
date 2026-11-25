# Data retention, translated from the "Retention Schedule" sheet.
# All periods are assumptions until the instructor reviews them (see variables.tf).

# ---------- S3: lab files ----------
# Versioning is needed for the "restore a deleted file" test and for AWS Backup of S3.
resource "aws_s3_bucket_versioning" "lab_files" {
  bucket = var.lab_files_bucket_id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lab_files" {
  bucket = var.lab_files_bucket_id

  rule {
    id     = "expire-lab-files"
    status = "Enabled"
    filter {}

    expiration {
      days = var.lab_files_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.old_versions_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.lab_files]
}

# ---------- S3: audit (CloudTrail) bucket ----------
# If the Lead turns on S3 Object Lock on this bucket, its lock period must be
# no longer than audit_retention_days, otherwise the expiry will not delete anything.
resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = var.audit_bucket_id

  rule {
    id     = "expire-audit-logs"
    status = "Enabled"
    filter {}

    expiration {
      days = var.audit_retention_days
    }
  }
}
