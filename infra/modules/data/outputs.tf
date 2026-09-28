output "table_name" {
  value = aws_dynamodb_table.runtime.name
}

output "table_arn" {
  value = aws_dynamodb_table.runtime.arn
}
