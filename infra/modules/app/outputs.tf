output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "function_arn" {
  value = aws_lambda_function.api.arn
}

output "function_url" {
  value = aws_lambda_function_url.api.function_url
}

output "function_url_domain" {
  value = trimsuffix(trimprefix(aws_lambda_function_url.api.function_url, "https://"), "/")
}
