data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name = "nettriage-${var.stage}-api"
  parameter_arns = [
    for name in [var.oidc_parameter, var.oidc_secret_parameter, var.database_url_parameter] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}

resource "aws_iam_role" "api" {
  name = local.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The only permission the API needs in Plan 1: writing its own logs.
resource "aws_iam_role_policy" "api_logs" {
  name = "write-own-logs"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.api.arn}:*"
    }]
  })
}

# Sessions, sign-in state and rate limits in the runtime table (spec §6.8). TransactWriteItems
# and BatchWriteItem are authorized by the item-level actions they perform.
resource "aws_iam_role_policy" "api_runtime_table" {
  name = "runtime-table"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
      ]
      Resource = var.runtime_table_arn
    }]
  })
}

# Its own settings and secrets, read once at cold start. SecureStrings use the AWS-managed
# aws/ssm key, whose key policy already lets this account's roles decrypt through SSM.
resource "aws_iam_role_policy" "api_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.parameter_arns
    }]
  })
}

resource "aws_lambda_function" "api" {
  function_name    = local.name
  role             = aws_iam_role.api.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "run.sh"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 1024
  timeout          = 29
  layers           = [var.lwa_layer_arn, var.otel_collector_layer_arn]

  environment {
    variables = {
      AWS_LAMBDA_EXEC_WRAPPER            = "/opt/bootstrap"
      AWS_LWA_PORT                       = "8080"
      AWS_LWA_READINESS_CHECK_PATH       = "/api/health"
      OPENTELEMETRY_COLLECTOR_CONFIG_URI = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT        = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL        = "http/protobuf"
      GRAFANA_OTLP_ENDPOINT              = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                  = var.grafana_otlp_auth
      NETTRIAGE_STAGE                    = var.stage
      NETTRIAGE_VERSION                  = var.app_version
      NETTRIAGE_SERVICE_NAME             = "nettriage-api"
      NETTRIAGE_RUNTIME_TABLE            = var.runtime_table_name
      NETTRIAGE_OIDC_PARAMETER           = var.oidc_parameter
      NETTRIAGE_OIDC_SECRET_PARAMETER    = var.oidc_secret_parameter
      NETTRIAGE_DATABASE_URL_PARAMETER   = var.database_url_parameter
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.api,
    aws_iam_role_policy.api_logs,
    aws_iam_role_policy.api_runtime_table,
    aws_iam_role_policy.api_parameters,
  ]
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "BUFFERED"
}
