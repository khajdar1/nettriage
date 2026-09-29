data "aws_caller_identity" "current" {}

locals {
  name = "nettriage-${var.stage}"
  # Bucket names are global. A hash of the account ID keeps ours unique without the account ID
  # itself, which would otherwise appear in every page's CSP header (as for the sign-in domain).
  bucket = "${local.name}-uploads-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
}

# Raw uploads (spec §5.6): private, TLS-only, SSE-S3, deleted after 30 days. Browsers PUT files
# here with a presigned URL from the API; the analyze worker (Plan 4b) reads them.
resource "aws_s3_bucket" "uploads" {
  bucket = local.bucket
}

resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TlsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.uploads.arn, "${aws_s3_bucket.uploads.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.uploads]
}

# Browsers PUT files straight from the app's pages, and from nowhere else. The browser adds
# Content-Length itself; the other signed headers are these.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = [var.app_origin]
    allowed_headers = ["content-type", "x-amz-checksum-sha256", "x-amz-meta-traceparent"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "expire-raw-uploads"
    status = "Enabled"

    filter {}

    expiration {
      days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# The uploads kill switch (spec §9.7). The API re-reads it every minute: "true" lets members
# upload, anything else pauses uploads. Terraform only creates it, so a deploy never switches
# uploads back on after the owner paused them.
resource "aws_ssm_parameter" "uploads_enabled" {
  #checkov:skip=CKV2_AWS_34:Not secret: an on/off flag.
  name  = "/nettriage/${var.stage}/kill/uploads-enabled"
  type  = "String"
  value = "true"

  lifecycle {
    ignore_changes = [value]
  }
}
