data "aws_region" "current" {}

locals {
  analyze = "${local.name}-analyze"
  analyze_parameter_arn = join("", [
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}",
    ":parameter${var.database_url_parameter}",
  ])
}

resource "aws_cloudwatch_log_group" "analyze" {
  name              = "/aws/lambda/${local.analyze}"
  retention_in_days = 7
}

resource "aws_iam_role" "analyze" {
  name = local.analyze
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "analyze_logs" {
  name = "write-own-logs"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.analyze.arn}:*"
    }]
  })
}

# The worker reads uploads and nothing else in the bucket (spec §6.8).
resource "aws_iam_role_policy" "analyze_uploads" {
  name = "read-uploads"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.uploads.arn}/orgs/*"
    }]
  })
}

# What the event source mapping does with the worker's role: receive, delete, and extend a
# message's visibility.
resource "aws_iam_role_policy" "analyze_queue" {
  name = "consume-analyze-queue"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility",
      ]
      Resource = aws_sqs_queue.analyze.arn
    }]
  })
}

# Its database URL (app_analyze), a SecureString the deploy writes, read once at cold start.
resource "aws_iam_role_policy" "analyze_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.analyze_parameter_arn
    }]
  })
}

# The same package as the API, with a plain handler: no web adapter (spec §3.5).
resource "aws_lambda_function" "analyze" {
  function_name    = local.analyze
  role             = aws_iam_role.analyze.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "nettriage.entrypoints.analyze.handler.handle"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 2048
  timeout          = local.analyze_timeout
  layers           = [var.otel_collector_layer_arn]

  environment {
    variables = {
      OPENTELEMETRY_COLLECTOR_CONFIG_URI = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT        = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL        = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT              = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                  = var.grafana_otlp_auth
      NETTRIAGE_STAGE                    = var.stage
      NETTRIAGE_VERSION                  = var.app_version
      NETTRIAGE_SERVICE_NAME             = "nettriage-analyze"
      NETTRIAGE_DATABASE_URL_PARAMETER   = var.database_url_parameter
      NETTRIAGE_UPLOADS_BUCKET           = aws_s3_bucket.uploads.bucket
      NETTRIAGE_TRIAGE_QUEUE_URL         = aws_sqs_queue.triage.id
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.analyze,
    aws_iam_role_policy.analyze_logs,
    aws_iam_role_policy.analyze_uploads,
    aws_iam_role_policy.analyze_queue,
    aws_iam_role_policy.analyze_parameters,
    aws_iam_role_policy.analyze_triage_queue,
  ]
}

# One message per invocation, and at most two at once: Neon's pooler and the account's low
# Lambda concurrency quota both prefer a small cap (spec §3.5, §13.2).
resource "aws_lambda_event_source_mapping" "analyze" {
  event_source_arn = aws_sqs_queue.analyze.arn
  function_name    = aws_lambda_function.analyze.arn
  batch_size       = 1

  scaling_config {
    maximum_concurrency = 2
  }
}
