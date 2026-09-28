locals {
  name = "nettriage-${var.stage}-api"
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
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api_logs]
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "BUFFERED"
}
