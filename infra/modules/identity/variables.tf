variable "stage" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.stage)
    error_message = "stage must be dev or prod."
  }
}

variable "app_domain" {
  type        = string
  description = "The app's CloudFront domain; sign-in returns to https://<app_domain>/api/auth/callback."
}

variable "oidc_parameter" {
  type        = string
  description = "SSM parameter for the app client's settings (JSON, not secret), which the API reads."
}

variable "oidc_secret_parameter" {
  type        = string
  description = "SSM SecureString parameter for the app client's secret, which the API reads."
}
