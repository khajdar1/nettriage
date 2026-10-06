variable "stage" {
  type = string
}

variable "lambda_zip_path" {
  type        = string
  description = "Path to dist/backend.zip built by tools/build_lambda.py; every function ships it."
}

variable "pg_client_zip_path" {
  type        = string
  description = "Path to dist/pg-client.zip built by tools/pack_pg_client.py: Postgres 17 for the layer."
}

variable "app_version" {
  type        = string
  description = "Git SHA the function reports in its telemetry."
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
  description = "SSM parameter holding app_ops's pooled database URL, which the deploy writes."
}

variable "backup_database_url_parameter" {
  type        = string
  description = "SSM parameter holding app_backup's direct database URL, which the deploy writes."
}

variable "runtime_table_name" {
  type = string
}

variable "runtime_table_arn" {
  type = string
}

variable "app_url" {
  type        = string
  description = "The app's CloudFront URL, which the probe fetches /api/health through."

  validation {
    condition     = can(regex("^https://", var.app_url))
    error_message = "The app's URL is https."
  }
}
