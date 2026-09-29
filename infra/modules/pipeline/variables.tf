variable "stage" {
  type = string
}

variable "app_origin" {
  type        = string
  description = "The app's origin (https://<distribution domain>), the only one that may PUT uploads."
}
