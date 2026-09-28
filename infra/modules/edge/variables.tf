variable "stage" {
  type = string
}

variable "api_origin_domain" {
  type        = string
  description = "Host of the API Lambda Function URL, without scheme or trailing slash."
}

variable "csp_connect_src_extra" {
  type        = list(string)
  default     = []
  description = "Extra origins for CSP connect-src (Plan 4 adds the uploads bucket)."
}
