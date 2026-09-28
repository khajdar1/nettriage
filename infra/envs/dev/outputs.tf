output "cloudfront_domain" {
  value = module.edge.distribution_domain
}

output "distribution_id" {
  value = module.edge.distribution_id
}

output "web_bucket" {
  value = module.edge.web_bucket
}

output "function_url" {
  value = module.app.function_url
}

output "sign_in_domain" {
  value = module.identity.sign_in_domain
}
