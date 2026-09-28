mock_provider "aws" {
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
  stage                    = "dev"
  lambda_zip_path          = "tests/fixtures/app.zip"
  app_version              = "test-sha"
  lwa_layer_arn            = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
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
