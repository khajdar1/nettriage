variable "aws_region" {
  type        = string
  default     = "eu-north-1"
  description = "The account's only Region for regional resources (spec Revision 2, R1)."
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
