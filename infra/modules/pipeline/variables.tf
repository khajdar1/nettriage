variable "stage" {
  type = string
}

variable "app_origin" {
  type        = string
  description = "The app's origin (https://<distribution domain>), the only one that may PUT uploads."
}

variable "lambda_zip_path" {
  type        = string
  description = "Path to dist/backend.zip built by tools/build_lambda.py; the API ships the same package."
}

variable "app_version" {
  type        = string
  description = "Git SHA the worker reports in its telemetry."
}

variable "runtime" {
  type    = string
  default = "python3.14"
}

variable "otel_collector_layer_arn" {
  type = string

  validation {
    condition     = can(regex("^arn:aws:lambda:eu-north-1:[0-9]{12}:layer:opentelemetry-collector-arm64-[0-9a-z_-]+:[0-9]+$", var.otel_collector_layer_arn))
    error_message = "Use the eu-north-1 arm64 OpenTelemetry collector layer."
  }
}

variable "grafana_otlp_endpoint" {
  type        = string
  description = "Grafana Cloud OTLP endpoint (not secret)."

  validation {
    condition     = can(regex("^https://", var.grafana_otlp_endpoint))
    error_message = "Use the Grafana Cloud OTLP https endpoint."
  }
}

variable "grafana_otlp_auth" {
  type        = string
  sensitive   = true
  description = "base64(instanceID:token) for a write-only telemetry token."
}

variable "database_url_parameter" {
  type        = string
  description = "SSM SecureString with app_analyze's pooled Neon URL, written by the deploy (tools/deploy)."
}
