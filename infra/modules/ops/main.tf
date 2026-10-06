data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name = "nettriage-${var.stage}"
  ops  = "${local.name}-ops"
  # Global names; a hash of the account ID keeps ours unique without exposing it, as for uploads.
  bucket = "${local.name}-backups-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
  parameter_arns = [
    for name in [var.database_url_parameter, var.backup_database_url_parameter] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
  # The jobs (Plan 7a §2), in UTC. A probe or check isn't retried: the next one comes in minutes.
  schedules = {
    probe         = { expression = "rate(5 minutes)", retries = 0 }
    check         = { expression = "cron(17 * * * ? *)", retries = 0 }
    backup        = { expression = "cron(0 2 * * ? *)", retries = 2 }
    cleanup       = { expression = "cron(0 3 * * ? *)", retries = 2 }
    restore_drill = { expression = "cron(0 4 ? * SUN *)", retries = 2 }
  }
}

# Nightly database dumps and their manifests (Plan 7a §2.3): private, TLS-only, SSE-S3, kept for
# 7 days (spec §5.7). Only the ops function writes them; the owner's session can read them.
resource "aws_s3_bucket" "backups" {
  bucket = local.bucket
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "backups" {
  bucket = aws_s3_bucket.backups.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TlsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.backups]
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "keep-7-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Postgres 17 for the backup and the drill, built from source for Lambda (tools/pg_client).
resource "aws_lambda_layer_version" "pg_client" {
  layer_name               = "${local.name}-pg-client"
  filename                 = var.pg_client_zip_path
  source_code_hash         = filebase64sha256(var.pg_client_zip_path)
  compatible_runtimes      = [var.runtime]
  compatible_architectures = ["arm64"]
}

resource "aws_cloudwatch_log_group" "ops" {
  name              = "/aws/lambda/${local.ops}"
  retention_in_days = 7
}

resource "aws_iam_role" "ops" {
  name = local.ops
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ops_logs" {
  name = "write-own-logs"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.ops.arn}:*"
    }]
  })
}

# app_ops's and app_backup's connection strings, which the deploy writes.
resource "aws_iam_role_policy" "ops_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.parameter_arns
    }]
  })
}

# The hourly check reads one key of its own; nothing else in the runtime table.
resource "aws_iam_role_policy" "ops_runtime_table" {
  name = "read-probe-key"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "dynamodb:GetItem"
      Resource  = var.runtime_table_arn
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = ["ops#probe"] } }
    }]
  })
}

resource "aws_iam_role_policy" "ops_backups" {
  name = "keep-backups"
  role = aws_iam_role.ops.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.backups.arn}/pg/*"
      },
      {
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.backups.arn
        Condition = { StringLike = { "s3:prefix" = ["pg/*"] } }
      },
    ]
  })
}

# The same package as the other functions, with the Postgres layer (Plan 7a §4).
resource "aws_lambda_function" "ops" {
  function_name    = local.ops
  role             = aws_iam_role.ops.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "nettriage.entrypoints.ops.handler.handle"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 1024
  timeout          = 600
  layers           = [var.otel_collector_layer_arn, aws_lambda_layer_version.pg_client.arn]

  ephemeral_storage {
    size = 2048
  }

  environment {
    variables = {
      OPENTELEMETRY_COLLECTOR_CONFIG_URI      = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT             = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL             = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT                   = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                       = var.grafana_otlp_auth
      NETTRIAGE_STAGE                         = var.stage
      NETTRIAGE_VERSION                       = var.app_version
      NETTRIAGE_SERVICE_NAME                  = "nettriage-ops"
      NETTRIAGE_DATABASE_URL_PARAMETER        = var.database_url_parameter
      NETTRIAGE_BACKUP_DATABASE_URL_PARAMETER = var.backup_database_url_parameter
      NETTRIAGE_RUNTIME_TABLE                 = var.runtime_table_name
      NETTRIAGE_BACKUPS_BUCKET                = aws_s3_bucket.backups.bucket
      NETTRIAGE_APP_URL                       = var.app_url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ops,
    aws_iam_role_policy.ops_logs,
    aws_iam_role_policy.ops_parameters,
    aws_iam_role_policy.ops_runtime_table,
    aws_iam_role_policy.ops_backups,
  ]
}

# EventBridge Scheduler invokes the function, with this role and no other right.
resource "aws_iam_role" "scheduler" {
  name = "${local.ops}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-ops"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.ops.arn
    }]
  })
}

resource "aws_scheduler_schedule" "ops" {
  for_each = local.schedules

  name                         = "${local.ops}-${replace(each.key, "_", "-")}"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ops.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ job = each.key })

    retry_policy {
      maximum_retry_attempts       = each.value.retries
      maximum_event_age_in_seconds = 3600
    }
  }
}
