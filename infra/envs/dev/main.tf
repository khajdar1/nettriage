# The API's SSM parameters. identity writes the first two; the deploy writes the database URL
# (tools/deploy/config.py, db_role_url_parameter). The function gets only these names.
locals {
  oidc_parameter         = "/nettriage/dev/api/oidc"
  oidc_secret_parameter  = "/nettriage/dev/api/oidc-client-secret"
  database_url_parameter = "/nettriage/dev/db/app-api-url"
}

module "data" {
  source = "../../modules/data"
  stage  = "dev"
}

module "app" {
  source                   = "../../modules/app"
  stage                    = "dev"
  lambda_zip_path          = var.lambda_zip_path
  app_version              = var.app_version
  lwa_layer_arn            = var.lwa_layer_arn
  otel_collector_layer_arn = var.otel_collector_layer_arn
  grafana_otlp_endpoint    = var.grafana_otlp_endpoint
  grafana_otlp_auth        = var.grafana_otlp_auth
  runtime_table_name       = module.data.table_name
  runtime_table_arn        = module.data.table_arn
  oidc_parameter           = local.oidc_parameter
  oidc_secret_parameter    = local.oidc_secret_parameter
  database_url_parameter   = local.database_url_parameter
}

module "identity" {
  source                = "../../modules/identity"
  stage                 = "dev"
  app_domain            = module.edge.distribution_domain
  oidc_parameter        = local.oidc_parameter
  oidc_secret_parameter = local.oidc_secret_parameter
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
