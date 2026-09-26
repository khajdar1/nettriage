data "aws_caller_identity" "current" {}

module "app" {
  source                   = "../../modules/app"
  stage                    = "dev"
  lambda_zip_path          = var.lambda_zip_path
  app_version              = var.app_version
  lwa_layer_arn            = var.lwa_layer_arn
  otel_collector_layer_arn = var.otel_collector_layer_arn
  grafana_otlp_endpoint    = var.grafana_otlp_endpoint
  grafana_otlp_auth        = var.grafana_otlp_auth
  # The deploy role may only create/attach roles that carry this boundary (ruling R20).
  permissions_boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/nettriage-dev-boundary"
}

module "edge" {
  source            = "../../modules/edge"
  stage             = "dev"
  api_origin_domain = module.app.function_url_domain
}

# Only this CloudFront distribution may invoke the Function URL. Both actions are granted
# to match current AWS guidance for OAC with Lambda Function URLs (spec §13.2).
resource "aws_lambda_permission" "cloudfront_invoke_url" {
  statement_id           = "AllowCloudFrontInvokeFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = module.app.function_name
  principal              = "cloudfront.amazonaws.com"
  source_arn             = module.edge.distribution_arn
  function_url_auth_type = "AWS_IAM"
}

resource "aws_lambda_permission" "cloudfront_invoke" {
  statement_id  = "AllowCloudFrontInvokeFunction"
  action        = "lambda:InvokeFunction"
  function_name = module.app.function_name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = module.edge.distribution_arn
}
