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
  # Computed ARNs are short random strings by default; these resources check that the ARNs
  # they are given look like ARNs.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-dev-analyze"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::nettriage-dev-uploads-12345678"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:eu-north-1:123456789012:log-group:/aws/lambda/nettriage-dev-analyze"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:eu-north-1:123456789012:function:nettriage-dev-analyze"
    }
  }
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze"
      id  = "https://sqs.eu-north-1.amazonaws.com/123456789012/nettriage-dev-analyze"
    }
  }
}

override_resource {
  target = aws_sqs_queue.analyze_dlq
  values = {
    arn = "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze-dlq"
  }
}

variables {
  stage                    = "dev"
  app_origin               = "https://d111111abcdef8.cloudfront.net"
  lambda_zip_path          = "../app/tests/fixtures/app.zip"
  app_version              = "test-sha"
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
  database_url_parameter   = "/nettriage/dev/db/app-analyze-url"
}

run "the_bucket_name_does_not_expose_the_account_id" {
  command = apply

  assert {
    condition     = aws_s3_bucket.uploads.bucket == "nettriage-dev-uploads-${substr(sha256("123456789012"), 0, 8)}"
    error_message = "The bucket is nettriage-<stage>-uploads-<8 hex of the account ID's hash>."
  }
  assert {
    condition     = !strcontains(aws_s3_bucket.uploads.bucket, "123456789012")
    error_message = "The CSP shows the bucket's name to everyone, so it must not hold the account ID."
  }
}

run "the_bucket_is_private_encrypted_and_tls_only" {
  command = apply

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.uploads.block_public_acls,
      aws_s3_bucket_public_access_block.uploads.block_public_policy,
      aws_s3_bucket_public_access_block.uploads.ignore_public_acls,
      aws_s3_bucket_public_access_block.uploads.restrict_public_buckets,
    ])
    error_message = "Public access is blocked (spec §5.6)."
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.uploads.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Uploads are encrypted with SSE-S3 (spec §5.6)."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.uploads.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false" && jsondecode(aws_s3_bucket_policy.uploads.policy).Statement[0].Effect == "Deny"
    error_message = "Requests without TLS are denied (spec §5.6)."
  }
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.uploads.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs are disabled."
  }
}

run "only_the_app_may_put_from_a_browser" {
  command = apply

  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_methods == toset(["PUT"])
    error_message = "Browsers may only PUT (spec §5.6)."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_origins == toset(["https://d111111abcdef8.cloudfront.net"])
    error_message = "Only the app's origin may PUT (spec §5.6)."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_headers == toset(["content-type", "x-amz-checksum-sha256", "x-amz-meta-traceparent"])
    error_message = "The browser may send the presigned PUT's signed headers and a content type."
  }
}

run "raw_uploads_are_deleted_after_30_days" {
  command = apply

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.uploads.rule).expiration).days == 30
    error_message = "Raw uploads are kept for 30 days (spec §5.7)."
  }
  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.uploads.rule).abort_incomplete_multipart_upload).days_after_initiation == 1
    error_message = "Incomplete multipart uploads are aborted after 1 day (spec §5.6)."
  }
}

run "uploads_start_enabled" {
  command = apply

  assert {
    condition     = aws_ssm_parameter.uploads_enabled.name == "/nettriage/dev/kill/uploads-enabled" && aws_ssm_parameter.uploads_enabled.value == "true"
    error_message = "The uploads kill switch starts on (spec §9.7)."
  }
  assert {
    condition     = output.uploads_enabled_parameter == "/nettriage/dev/kill/uploads-enabled"
    error_message = "The API is told the switch's name."
  }
}

run "every_raw_upload_reaches_the_analyze_queue" {
  command = apply

  assert {
    condition     = one(aws_s3_bucket_notification.uploads.queue).queue_arn == aws_sqs_queue.analyze.arn
    error_message = "The uploads bucket announces uploads to the analyze queue (spec §4.2)."
  }
  assert {
    condition     = one(aws_s3_bucket_notification.uploads.queue).events == toset(["s3:ObjectCreated:*"])
    error_message = "Every finished upload is announced, however it was written."
  }
  assert {
    condition     = one(aws_s3_bucket_notification.uploads.queue).filter_prefix == "orgs/" && one(aws_s3_bucket_notification.uploads.queue).filter_suffix == "/raw"
    error_message = "Only raw uploads (orgs/{org_id}/uploads/{upload_id}/raw) are announced (spec §5.6)."
  }
  assert {
    condition     = jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Principal.Service == "s3.amazonaws.com" && jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Action == "sqs:SendMessage"
    error_message = "S3 may send to the queue, and do nothing else."
  }
  assert {
    condition     = jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == aws_s3_bucket.uploads.arn && jsondecode(aws_sqs_queue_policy.analyze.policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Only this account's uploads bucket may send (confused-deputy protection)."
  }
}

run "a_message_failed_three_times_waits_in_the_dead_letter_queue" {
  command = apply

  assert {
    condition     = jsondecode(aws_sqs_queue.analyze.redrive_policy).maxReceiveCount == 3 && jsondecode(aws_sqs_queue.analyze.redrive_policy).deadLetterTargetArn == "arn:aws:sqs:eu-north-1:123456789012:nettriage-dev-analyze-dlq"
    error_message = "After 3 receives a message moves to the DLQ (spec §3.2, §8.6)."
  }
  assert {
    condition     = aws_sqs_queue.analyze.visibility_timeout_seconds == 6 * aws_lambda_function.analyze.timeout
    error_message = "The visibility timeout is 6 times the worker's timeout (spec §3.5)."
  }
  assert {
    condition     = aws_sqs_queue.analyze_dlq.message_retention_seconds == 1209600
    error_message = "Dead letters are kept for 14 days, SQS's maximum."
  }
  assert {
    condition     = aws_sqs_queue.analyze.sqs_managed_sse_enabled && aws_sqs_queue.analyze_dlq.sqs_managed_sse_enabled
    error_message = "Both queues are encrypted at rest with SQS-managed keys."
  }
}

run "the_worker_is_arm64_python_with_2048_mb_and_300_s" {
  command = apply

  assert {
    condition     = aws_lambda_function.analyze.function_name == "nettriage-dev-analyze"
    error_message = "Names follow nettriage-<stage>-<name>."
  }
  assert {
    condition     = aws_lambda_function.analyze.runtime == "python3.14" && aws_lambda_function.analyze.architectures == tolist(["arm64"])
    error_message = "The worker runs on python3.14, arm64."
  }
  assert {
    condition     = aws_lambda_function.analyze.memory_size == 2048 && aws_lambda_function.analyze.timeout == 300
    error_message = "The worker uses 2048 MB and a 300 s timeout (spec §3.5)."
  }
  assert {
    condition     = aws_lambda_function.analyze.handler == "nettriage.entrypoints.analyze.handler.handle"
    error_message = "The worker's handler is the analyze entry point, without the web adapter."
  }
  assert {
    condition     = aws_lambda_function.analyze.layers == tolist(["arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"])
    error_message = "The worker has the OpenTelemetry collector layer and no other."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_SERVICE_NAME"] == "nettriage-analyze"
    error_message = "The worker's telemetry is service.name nettriage-analyze (spec §9.1)."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_DATABASE_URL_PARAMETER"] == "/nettriage/dev/db/app-analyze-url"
    error_message = "The worker connects as app_analyze (spec §5.4)."
  }
  assert {
    condition     = aws_lambda_function.analyze.environment[0].variables["NETTRIAGE_UPLOADS_BUCKET"] == aws_s3_bucket.uploads.bucket
    error_message = "The worker reads from the uploads bucket."
  }
  assert {
    condition     = aws_cloudwatch_log_group.analyze.name == "/aws/lambda/nettriage-dev-analyze" && aws_cloudwatch_log_group.analyze.retention_in_days == 7
    error_message = "The worker's logs are kept 7 days (spec §9.3)."
  }
}

run "the_worker_takes_one_message_at_a_time_and_runs_at_most_twice_at_once" {
  command = apply

  assert {
    condition     = aws_lambda_event_source_mapping.analyze.event_source_arn == aws_sqs_queue.analyze.arn && aws_lambda_event_source_mapping.analyze.batch_size == 1
    error_message = "The worker reads the analyze queue one message at a time (spec §3.5)."
  }
  assert {
    condition     = one(aws_lambda_event_source_mapping.analyze.scaling_config).maximum_concurrency == 2
    error_message = "The event source mapping caps the worker at 2 concurrent runs (spec §3.5)."
  }
}

run "the_worker_may_read_uploads_its_queue_and_its_database_url_only" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role_policy.analyze_uploads.policy).Statement[0].Action == "s3:GetObject" && jsondecode(aws_iam_role_policy.analyze_uploads.policy).Statement[0].Resource == "arn:aws:s3:::nettriage-dev-uploads-12345678/orgs/*"
    error_message = "The worker may only get objects under orgs/ (spec §6.8)."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.analyze_queue.policy).Statement[0].Action) == toset(["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]) && jsondecode(aws_iam_role_policy.analyze_queue.policy).Statement[0].Resource == aws_sqs_queue.analyze.arn
    error_message = "The worker may only consume the analyze queue (spec §6.8)."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.analyze_parameters.policy).Statement[0].Resource == "arn:aws:ssm:eu-north-1:123456789012:parameter/nettriage/dev/db/app-analyze-url"
    error_message = "The worker may only read its own database URL (spec §6.8)."
  }
}
