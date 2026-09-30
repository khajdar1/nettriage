locals {
  # The analyze worker's timeout; SQS hides a message for 6 times as long (spec §3.5).
  analyze_timeout = 300
}

# Each finished upload becomes one message (spec §4.2). A message the worker fails three times
# moves to the dead-letter queue, kept 14 days for a look and a redrive (spec §8.6).
resource "aws_sqs_queue" "analyze_dlq" {
  name                      = "${local.name}-analyze-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "analyze" {
  name                       = "${local.name}-analyze"
  visibility_timeout_seconds = local.analyze_timeout * 6
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.analyze_dlq.arn
    maxReceiveCount     = 3
  })
}

# Only this account's uploads bucket may send to the queue.
resource "aws_sqs_queue_policy" "analyze" {
  queue_url = aws_sqs_queue.analyze.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "UploadsBucketSends"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.analyze.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.uploads.arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# Every raw upload (orgs/{org_id}/uploads/{upload_id}/raw) is announced to the queue. S3 checks
# that it may send there, so the queue policy comes first.
resource "aws_s3_bucket_notification" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  queue {
    queue_arn     = aws_sqs_queue.analyze.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "orgs/"
    filter_suffix = "/raw"
  }

  depends_on = [aws_sqs_queue_policy.analyze]
}
