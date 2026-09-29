mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  stage      = "dev"
  app_origin = "https://d111111abcdef8.cloudfront.net"
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
