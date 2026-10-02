locals {
  triage = "${local.name}-triage"
  # One finding per run (the owner's decision, Plan 5b): an answer and its repair, each with
  # a 20 s timeout and 3 retries, fit in 240 s. SQS hides a message for 6 times as long.
  triage_timeout = 240
  triage_parameter_arns = [
    for name in [var.triage_database_url_parameter, aws_ssm_parameter.ai_enabled.name] :
    "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${name}"
  ]
}

# The AI kill switch (spec §9.7). The triage worker re-reads it every minute: "true" lets it
# call the model, anything else skips the call (`ai_disabled`). Terraform only creates it, so a
# deploy never switches the AI back on after the owner paused it.
resource "aws_ssm_parameter" "ai_enabled" {
  #checkov:skip=CKV2_AWS_34:Not secret: an on/off flag.
  name  = "/nettriage/${var.stage}/kill/ai-enabled"
  type  = "String"
  value = "true"

  lifecycle {
    ignore_changes = [value]
  }
}

# One message per finding to explain (spec §4.2). A message the worker hands back three times
# moves to the dead-letter queue, kept 14 days for a look and a redrive (spec §8.6).
resource "aws_sqs_queue" "triage_dlq" {
  name                      = "${local.triage}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "triage" {
  name                       = local.triage
  visibility_timeout_seconds = local.triage_timeout * 6
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.triage_dlq.arn
    maxReceiveCount     = 3
  })
}

# The analyze worker queues each analyzed upload's most severe findings.
resource "aws_iam_role_policy" "analyze_triage_queue" {
  name = "send-to-triage-queue"
  role = aws_iam_role.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.triage.arn
    }]
  })
}

resource "aws_cloudwatch_log_group" "triage" {
  name              = "/aws/lambda/${local.triage}"
  retention_in_days = 7
}

# The $5 budget action in the bootstrap denies this role Bedrock (spec §6.7), by name.
resource "aws_iam_role" "triage" {
  name = local.triage
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "triage_logs" {
  name = "write-own-logs"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.triage.arn}:*"
    }]
  })
}

resource "aws_iam_role_policy" "triage_queue" {
  name = "consume-triage-queue"
  role = aws_iam_role.triage.id
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
      Resource = aws_sqs_queue.triage.arn
    }]
  })
}

# Its database URL (app_triage), which the deploy writes, and the AI kill switch.
resource "aws_iam_role_policy" "triage_parameters" {
  name = "read-own-parameters"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameters"
      Resource = local.triage_parameter_arns
    }]
  })
}

# AI budgets live in the runtime table (spec §5.5); the worker may touch their items and no
# others: not sessions, sign-in states, rate limits or idempotency keys.
resource "aws_iam_role_policy" "triage_budgets" {
  name = "update-ai-budgets"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "dynamodb:UpdateItem"
      Resource = var.runtime_table_arn
      Condition = {
        "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["BUDGET#*", "GBUDGET#*"] }
      }
    }]
  })
}

# One model, on demand in its own Region: the account can't use cross-Region inference
# profiles (spec Revision 2, R4). Converse needs bedrock:InvokeModel.
resource "aws_iam_role_policy" "triage_bedrock" {
  name = "invoke-triage-model"
  role = aws_iam_role.triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock:InvokeModel"
      Resource = "arn:aws:bedrock:${var.bedrock_region}::foundation-model/${var.bedrock_model_id}"
    }]
  })
}

# The same package as the API, with a plain handler (spec §3.5).
resource "aws_lambda_function" "triage" {
  function_name    = local.triage
  role             = aws_iam_role.triage.arn
  runtime          = var.runtime
  architectures    = ["arm64"]
  handler          = "nettriage.entrypoints.triage.handler.handle"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  memory_size      = 512
  timeout          = local.triage_timeout
  layers           = [var.otel_collector_layer_arn]

  environment {
    variables = {
      OPENTELEMETRY_COLLECTOR_CONFIG_URI                 = "/var/task/collector.yaml"
      OTEL_EXPORTER_OTLP_ENDPOINT                        = "http://localhost:4318"
      OTEL_EXPORTER_OTLP_PROTOCOL                        = "http/protobuf"
      OTEL_SEMCONV_STABILITY_OPT_IN                      = "gen_ai_latest_experimental"
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"
      GRAFANA_OTLP_ENDPOINT                              = var.grafana_otlp_endpoint
      GRAFANA_OTLP_AUTH                                  = var.grafana_otlp_auth
      NETTRIAGE_STAGE                                    = var.stage
      NETTRIAGE_VERSION                                  = var.app_version
      NETTRIAGE_SERVICE_NAME                             = "nettriage-triage"
      NETTRIAGE_DATABASE_URL_PARAMETER                   = var.triage_database_url_parameter
      NETTRIAGE_RUNTIME_TABLE                            = var.runtime_table_name
      NETTRIAGE_AI_ENABLED_PARAMETER                     = aws_ssm_parameter.ai_enabled.name
      NETTRIAGE_BEDROCK_REGION                           = var.bedrock_region
      NETTRIAGE_BEDROCK_MODEL_ID                         = var.bedrock_model_id
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.triage,
    aws_iam_role_policy.triage_logs,
    aws_iam_role_policy.triage_queue,
    aws_iam_role_policy.triage_parameters,
    aws_iam_role_policy.triage_budgets,
    aws_iam_role_policy.triage_bedrock,
  ]
}

# One finding per run, at most two at once (spec §3.5, the owner's decision in Plan 5b). The
# worker answers with a partial batch response, so a failed message alone is delivered again.
resource "aws_lambda_event_source_mapping" "triage" {
  event_source_arn        = aws_sqs_queue.triage.arn
  function_name           = aws_lambda_function.triage.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}
