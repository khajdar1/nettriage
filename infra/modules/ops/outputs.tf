output "function_name" {
  value       = aws_lambda_function.ops.function_name
  description = "The ops function, which `just restore-drill-<stage>` invokes."
}

output "backups_bucket" {
  value = aws_s3_bucket.backups.bucket
}
