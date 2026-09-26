mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  # The default mock for a computed "arn" attribute is a short random string, not
  # ARN-shaped. aws_cloudfront_distribution.main validates its function_association
  # ARNs look like ARNs, so give aws_cloudfront_function's computed arn a realistic
  # value (ruling R23).
  mock_resource "aws_cloudfront_function" {
    defaults = {
      arn = "arn:aws:cloudfront::123456789012:function/nettriage-dev-fn"
    }
  }
}

variables {
  stage             = "dev"
  api_origin_domain = "abc123.lambda-url.us-east-1.on.aws"
}

run "csp_matches_the_spec_exactly" {
  command = apply

  assert {
    condition     = output.csp == "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'; upgrade-insecure-requests"
    error_message = "The CSP must match spec §6.7."
  }
}

run "extra_connect_sources_are_appended" {
  command = apply

  variables {
    csp_connect_src_extra = ["https://nettriage-dev-uploads.s3.us-east-1.amazonaws.com"]
  }

  assert {
    condition     = strcontains(output.csp, "connect-src 'self' https://nettriage-dev-uploads.s3.us-east-1.amazonaws.com;")
    error_message = "Extra connect-src origins must follow 'self'."
  }
}

run "api_behavior_is_https_only_uncached_and_gated" {
  command = apply

  assert {
    condition = length([
      for b in aws_cloudfront_distribution.main.ordered_cache_behavior : b
      if b.path_pattern == "/api/*" && b.viewer_protocol_policy == "https-only" && length(b.function_association) == 1
    ]) == 1
    error_message = "/api/* must be HTTPS-only and gated by the edge session check."
  }
}

run "web_bucket_is_private_and_functions_use_js2" {
  command = apply

  assert {
    condition     = aws_s3_bucket_public_access_block.web.block_public_policy && aws_s3_bucket_public_access_block.web.restrict_public_buckets
    error_message = "The web bucket must stay private (CloudFront reads it through OAC)."
  }
  assert {
    condition     = aws_cloudfront_function.api_edge_check.runtime == "cloudfront-js-2.0" && aws_cloudfront_function.spa_rewrite.runtime == "cloudfront-js-2.0"
    error_message = "CloudFront Functions use the cloudfront-js-2.0 runtime."
  }
}
