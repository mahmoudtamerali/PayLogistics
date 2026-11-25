# AWS Backup: vault + daily plan + selection (DynamoDB and S3).
# No copy_action on purpose: backups stay in one region (PDPL cross-border rule).
# Everything here is off unless enable_backup = true.

resource "aws_backup_vault" "securecare" {
  count = var.enable_backup ? 1 : 0
  name  = "securecare-backup-vault"
  # Uses the default AWS Backup KMS key. Add kms_key_arn here if the team creates its own key.
}

resource "aws_backup_plan" "daily" {
  count = var.enable_backup ? 1 : 0
  name  = "securecare-daily-35d"

  rule {
    rule_name         = "daily-0300-utc"
    target_vault_name = aws_backup_vault.securecare[0].name
    schedule          = "cron(0 3 * * ? *)"

    lifecycle {
      delete_after = var.backup_retention_days
    }
  }
}

data "aws_iam_policy_document" "backup_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  count              = var.enable_backup ? 1 : 0
  name               = "securecare-backup-role"
  assume_role_policy = data.aws_iam_policy_document.backup_assume.json
}

resource "aws_iam_role_policy_attachment" "backup_service" {
  count      = var.enable_backup ? 1 : 0
  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role_policy_attachment" "backup_restore" {
  count      = var.enable_backup ? 1 : 0
  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

resource "aws_iam_role_policy_attachment" "backup_s3" {
  count      = var.enable_backup ? 1 : 0
  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/AWSBackupServiceRolePolicyForS3Backup"
}

resource "aws_iam_role_policy_attachment" "restore_s3" {
  count      = var.enable_backup ? 1 : 0
  role       = aws_iam_role.backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/AWSBackupServiceRolePolicyForS3Restore"
}

resource "aws_backup_selection" "data" {
  count        = var.enable_backup ? 1 : 0
  name         = "securecare-data"
  iam_role_arn = aws_iam_role.backup[0].arn
  plan_id      = aws_backup_plan.daily[0].id

  resources = concat(var.dynamodb_table_arns, var.s3_bucket_arns)
}
