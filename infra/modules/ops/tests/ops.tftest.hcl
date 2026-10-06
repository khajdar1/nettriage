mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-dev-ops"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::nettriage-dev-backups-12345678"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-north-1:123456789012:log-group:/aws/lambda/nettriage-dev-ops"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:eu-north-1:123456789012:function:nettriage-dev-ops"
    }
  }
  mock_resource "aws_lambda_layer_version" {
    defaults = {
      arn = "arn:aws:lambda:eu-north-1:123456789012:layer:nettriage-dev-pg-client:1"
    }
  }
}

variables {
  stage                         = "dev"
  lambda_zip_path               = "../app/tests/fixtures/app.zip"
  pg_client_zip_path            = "../app/tests/fixtures/app.zip"
  app_version                   = "test-sha"
  otel_collector_layer_arn      = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint         = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth             = "dGVzdDp0ZXN0"
  database_url_parameter        = "/nettriage/dev/db/app-ops-url"
  backup_database_url_parameter = "/nettriage/dev/db/app-backup-url"
  runtime_table_name            = "nettriage-dev-runtime"
  runtime_table_arn             = "arn:aws:dynamodb:eu-north-1:123456789012:table/nettriage-dev-runtime"
  app_url                       = "https://d111111abcdef8.cloudfront.net"
}

run "the_function_has_room_for_a_dump_and_its_restore" {
  command = apply

  assert {
    condition     = aws_lambda_function.ops.handler == "nettriage.entrypoints.ops.handler.handle"
    error_message = "The ops function runs the ops handler from the shared package."
  }
  assert {
    condition = (
      aws_lambda_function.ops.memory_size == 1024 &&
      aws_lambda_function.ops.timeout == 600 &&
      one(aws_lambda_function.ops.ephemeral_storage).size == 2048
    )
    error_message = "1024 MB, 600 s and 2 GB of /tmp hold a dump and its restore (Plan 7a §4)."
  }
  assert {
    condition     = aws_lambda_function.ops.architectures == tolist(["arm64"])
    error_message = "The layer's Postgres is built for arm64."
  }
  assert {
    condition = (
      contains(aws_lambda_function.ops.layers, aws_lambda_layer_version.pg_client.arn) &&
      contains(aws_lambda_function.ops.layers, var.otel_collector_layer_arn)
    )
    error_message = "The function has the Postgres layer and the telemetry collector."
  }
  assert {
    condition     = aws_lambda_layer_version.pg_client.compatible_architectures == toset(["arm64"])
    error_message = "The Postgres layer is for arm64 only."
  }
}

run "the_function_knows_its_parameters_bucket_and_the_apps_url" {
  command = apply

  assert {
    condition = (
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_SERVICE_NAME == "nettriage-ops" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_DATABASE_URL_PARAMETER == "/nettriage/dev/db/app-ops-url" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_BACKUP_DATABASE_URL_PARAMETER == "/nettriage/dev/db/app-backup-url" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_BACKUPS_BUCKET == aws_s3_bucket.backups.bucket &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_RUNTIME_TABLE == "nettriage-dev-runtime" &&
      aws_lambda_function.ops.environment[0].variables.NETTRIAGE_APP_URL == "https://d111111abcdef8.cloudfront.net"
    )
    error_message = "The function is told its two connection strings, its bucket, the runtime table and the app's URL."
  }
}

run "five_schedules_name_their_jobs" {
  command = apply

  assert {
    condition = {
      for job, schedule in aws_scheduler_schedule.ops : job => schedule.schedule_expression
      } == {
      probe         = "rate(5 minutes)"
      check         = "cron(17 * * * ? *)"
      backup        = "cron(0 2 * * ? *)"
      cleanup       = "cron(0 3 * * ? *)"
      restore_drill = "cron(0 4 ? * SUN *)"
    }
    error_message = "Probe every 5 minutes, check hourly, back up nightly, clean up daily, drill weekly (Plan 7a §2)."
  }
  assert {
    condition = alltrue([
      for job, schedule in aws_scheduler_schedule.ops :
      jsondecode(one(schedule.target).input).job == job && schedule.schedule_expression_timezone == "UTC"
    ])
    error_message = "Each schedule invokes the ops function with its own job's name, in UTC."
  }
  assert {
    condition = (
      one(one(aws_scheduler_schedule.ops["probe"].target).retry_policy).maximum_retry_attempts == 0 &&
      one(one(aws_scheduler_schedule.ops["check"].target).retry_policy).maximum_retry_attempts == 0
    )
    error_message = "A probe or check isn't retried: the next one comes in minutes."
  }
}

run "the_scheduler_may_only_invoke_the_ops_function" {
  command = apply

  assert {
    condition = (
      jsondecode(aws_iam_role_policy.scheduler_invoke.policy).Statement[0].Action == "lambda:InvokeFunction" &&
      jsondecode(aws_iam_role_policy.scheduler_invoke.policy).Statement[0].Resource == aws_lambda_function.ops.arn
    )
    error_message = "The schedules' role may invoke the ops function and nothing else."
  }
  assert {
    condition     = jsondecode(aws_iam_role.scheduler.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Only this account's schedules may assume the role."
  }
}

run "the_backups_bucket_is_private_encrypted_tls_only_and_keeps_7_days" {
  command = apply

  assert {
    condition     = aws_s3_bucket.backups.bucket == "nettriage-dev-backups-${substr(sha256("123456789012"), 0, 8)}"
    error_message = "The bucket is nettriage-<stage>-backups-<8 hex of the account ID's hash> (Plan 7a §4)."
  }
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.backups.block_public_acls,
      aws_s3_bucket_public_access_block.backups.block_public_policy,
      aws_s3_bucket_public_access_block.backups.ignore_public_acls,
      aws_s3_bucket_public_access_block.backups.restrict_public_buckets,
    ])
    error_message = "Public access is blocked."
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.backups.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Backups are encrypted with SSE-S3."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.backups.policy).Statement[0].Effect == "Deny" && jsondecode(aws_s3_bucket_policy.backups.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "Requests without TLS are denied."
  }
  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.backups.rule).expiration).days == 7
    error_message = "Backups are kept for 7 days (spec §5.7)."
  }
}

run "the_function_may_only_do_its_jobs" {
  command = apply

  assert {
    condition = toset(jsondecode(aws_iam_role_policy.ops_parameters.policy).Statement[0].Resource) == toset([
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-ops-url",
      "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-backup-url",
    ])
    error_message = "The function reads its own two connection strings and no other parameter."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.ops_runtime_table.policy).Statement[0].Action == "dynamodb:GetItem" &&
      jsondecode(aws_iam_role_policy.ops_runtime_table.policy).Statement[0].Condition["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] == ["ops#probe"]
    )
    error_message = "The hourly check may read its own probe key in the runtime table, and nothing else."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[0].Action) == toset(["s3:PutObject", "s3:GetObject"]) && jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[0].Resource == "${aws_s3_bucket.backups.arn}/pg/*"
    error_message = "The function may put and get backups under pg/ only."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[1].Action == "s3:ListBucket" && jsondecode(aws_iam_role_policy.ops_backups.policy).Statement[1].Condition.StringLike["s3:prefix"] == ["pg/*"]
    error_message = "The function may list the backups under pg/ to find the newest."
  }
}
