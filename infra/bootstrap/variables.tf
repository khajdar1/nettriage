variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_owner" {
  type        = string
  description = "GitHub user or organization that owns the repository."
}

variable "github_repo" {
  type    = string
  default = "nettriage"
}

variable "budget_email" {
  type        = string
  description = "Email address for budget and cost anomaly alerts."
}

variable "anomaly_monitor_arn" {
  type        = string
  default     = ""
  description = "ARN of the account's existing Cost Anomaly Detection services monitor. Empty = no subscription."
}
