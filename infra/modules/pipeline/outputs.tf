output "uploads_bucket" {
  value = aws_s3_bucket.uploads.bucket
}

output "uploads_bucket_arn" {
  value = aws_s3_bucket.uploads.arn
}

output "uploads_origin" {
  description = "The bucket's regional origin, which presigned PUTs use and the CSP allows."
  value       = "https://${aws_s3_bucket.uploads.bucket_regional_domain_name}"
}

output "uploads_enabled_parameter" {
  value = aws_ssm_parameter.uploads_enabled.name
}

output "ai_enabled_parameter" {
  value = aws_ssm_parameter.ai_enabled.name
}

output "triage_queue_url" {
  value = aws_sqs_queue.triage.id
}

output "triage_queue_arn" {
  value = aws_sqs_queue.triage.arn
}
