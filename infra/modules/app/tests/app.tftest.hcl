mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  # The default mock for a computed "arn" attribute is a short random string, not
  # ARN-shaped. aws_lambda_function.role validates its value looks like an ARN,
  # so give aws_iam_role.api's computed arn a realistic value.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-dev-api"
    }
  }
}

variables {
  stage                     = "dev"
  lambda_zip_path           = "tests/fixtures/app.zip"
  app_version               = "test-sha"
  lwa_layer_arn             = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
  otel_collector_layer_arn  = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint     = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth         = "dGVzdDp0ZXN0"
  runtime_table_name        = "nettriage-dev-runtime"
  runtime_table_arn         = "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
  oidc_parameter            = "/nettriage/dev/api/oidc"
  oidc_secret_parameter     = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter    = "/nettriage/dev/db/app-api-url"
  uploads_bucket            = "nettriage-dev-uploads-12345678"
  uploads_bucket_arn        = "arn:aws:s3:::nettriage-dev-uploads-12345678"
  uploads_enabled_parameter = "/nettriage/dev/kill/uploads-enabled"
}

run "function_is_arm64_python_behind_iam_auth" {
  command = apply

  assert {
    condition     = aws_lambda_function.api.function_name == "nettriage-dev-api"
    error_message = "Names follow nettriage-<stage>-<name>."
  }
  assert {
    condition     = aws_lambda_function.api.runtime == "python3.14" && aws_lambda_function.api.architectures == tolist(["arm64"])
    error_message = "The API runs on python3.14, arm64."
  }
  assert {
    condition     = aws_lambda_function.api.memory_size == 1024 && aws_lambda_function.api.timeout == 29
    error_message = "The API uses 1024 MB and a 29 s timeout (spec §3.5)."
  }
  assert {
    condition     = aws_lambda_function_url.api.authorization_type == "AWS_IAM" && aws_lambda_function_url.api.invoke_mode == "BUFFERED"
    error_message = "The Function URL requires IAM auth (only CloudFront signs requests)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["AWS_LAMBDA_EXEC_WRAPPER"] == "/opt/bootstrap"
    error_message = "The Lambda Web Adapter must wrap the runtime."
  }
}

run "logs_are_kept_seven_days" {
  command = apply

  assert {
    condition     = aws_cloudwatch_log_group.api.retention_in_days == 7
    error_message = "Log retention is 7 days (spec §9.3)."
  }
}

run "rejects_an_x86_adapter_layer" {
  command = plan

  variables {
    lwa_layer_arn = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerX86:30"
  }

  expect_failures = [var.lwa_layer_arn]
}

run "rejects_a_layer_from_another_region" {
  command = plan

  variables {
    lwa_layer_arn = "arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerArm64:30"
  }

  expect_failures = [var.lwa_layer_arn]
}

run "rejects_a_non_https_otlp_endpoint" {
  command = plan

  variables {
    grafana_otlp_endpoint = "http://example.com/otlp"
  }

  expect_failures = [var.grafana_otlp_endpoint]
}

run "the_api_reads_only_its_own_table_and_parameters" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.api_runtime_table.policy).Statement[0].Resource == "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
    error_message = "DynamoDB access is limited to the runtime table (spec §6.8)."
  }
  assert {
    condition = toset(jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[0].Resource) == toset([
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/api/oidc",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/api/oidc-client-secret",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-api-url",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/kill/uploads-enabled",
    ])
    error_message = "The API reads only its own parameters and the uploads kill switch (spec §6.8, §9.7)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_DATABASE_URL_PARAMETER"] == "/nettriage/dev/db/app-api-url"
    error_message = "The function gets parameter names, never secret values."
  }
}

run "the_api_may_only_put_uploads_under_orgs" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.api_uploads.policy).Statement[0].Action == "s3:PutObject"
    error_message = "The API only puts objects, for presigned PUTs; it never reads uploads."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.api_uploads.policy).Statement[0].Resource == "arn:aws:s3:::nettriage-dev-uploads-12345678/orgs/*"
    error_message = "Presigned PUTs may only write under orgs/ in the uploads bucket (spec §5.6)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["NETTRIAGE_UPLOADS_BUCKET"] == "nettriage-dev-uploads-12345678" && aws_lambda_function.api.environment[0].variables["NETTRIAGE_UPLOADS_ENABLED_PARAMETER"] == "/nettriage/dev/kill/uploads-enabled"
    error_message = "The API is told the bucket and the kill switch's name, never values."
  }
}
