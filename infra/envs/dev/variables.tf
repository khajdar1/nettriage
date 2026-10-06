variable "lambda_zip_path" {
  type = string
}

variable "app_version" {
  type = string
}

variable "lwa_layer_arn" {
  type = string
}

variable "otel_collector_layer_arn" {
  type = string
}

variable "grafana_otlp_endpoint" {
  type = string
}

variable "grafana_otlp_auth" {
  type      = string
  sensitive = true
}

variable "bedrock_region" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

variable "pg_client_zip_path" {
  type        = string
  description = "Path to the CI-built dist/pg-client.zip: Postgres 17 for the ops function's layer."
}
