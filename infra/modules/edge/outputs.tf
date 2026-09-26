output "distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "distribution_arn" {
  value = aws_cloudfront_distribution.main.arn
}

output "distribution_domain" {
  value = aws_cloudfront_distribution.main.domain_name
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "csp" {
  value = local.csp
}
