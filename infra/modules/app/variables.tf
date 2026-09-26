variable "stage" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.stage)
    error_message = "stage must be dev or prod."
  }
}

variable "lambda_zip_path" {
  type        = string
  description = "Path to dist/backend.zip built by tools/build_lambda.py."
}

variable "app_version" {
  type        = string
  description = "Git SHA reported by /api/health."
}

variable "lwa_layer_arn" {
  type = string

  validation {
    condition     = can(regex("^arn:aws:lambda:us-east-1:[0-9]{12}:layer:LambdaAdapterLayerArm64:[0-9]+$", var.lwa_layer_arn))
    error_message = "Use the us-east-1 arm64 Lambda Web Adapter layer (LambdaAdapterLayerArm64)."
  }
}

variable "otel_collector_layer_arn" {
  type = string

  validation {
    condition     = can(regex("^arn:aws:lambda:us-east-1:[0-9]{12}:layer:opentelemetry-collector-arm64-[0-9a-z_-]+:[0-9]+$", var.otel_collector_layer_arn))
    error_message = "Use the us-east-1 arm64 OpenTelemetry collector layer."
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

variable "runtime" {
  type    = string
  default = "python3.14"
}

variable "permissions_boundary_arn" {
  type        = string
  description = "ARN of the permissions boundary every stage role must carry (bootstrap's nettriage-<stage>-boundary)."
}
