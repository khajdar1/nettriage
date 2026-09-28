output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "sign_in_domain" {
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
}
