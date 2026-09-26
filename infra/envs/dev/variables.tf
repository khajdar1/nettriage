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
