# Cognito (spec §6.1): one user pool per stage on the Essentials plan, self-service sign-up with
# a verified email, mandatory TOTP MFA, and a confidential app client for the API's
# authorization-code flow with PKCE. The API reads the client's settings and secret from SSM.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name       = "nettriage-${var.stage}"
  app_origin = "https://${var.app_domain}"
  region     = data.aws_region.current.region
  # Prefix domains are unique per Region across all of AWS. A hash of the account ID keeps ours
  # unique without putting the account ID in a public URL.
  domain_prefix = "${local.name}-${substr(sha256(data.aws_caller_identity.current.account_id), 0, 8)}"
}

resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  user_pool_tier           = "ESSENTIALS"
  deletion_protection      = var.stage == "prod" ? "ACTIVE" : "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "ON"

  username_configuration {
    case_sensitive = false
  }

  software_token_mfa_configuration {
    enabled = true
  }

  # At least 12 characters and no composition rules (NIST SP 800-63B); TOTP is mandatory.
  password_policy {
    minimum_length                   = 12
    require_lowercase                = false
    require_uppercase                = false
    require_numbers                  = false
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = local.domain_prefix
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${local.app_origin}/api/auth/callback"]
  logout_urls                          = ["${local.app_origin}/"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  # The API reads the ID token once and discards every token, so each lives as briefly as
  # Cognito allows. The sign-in session itself gets Cognito's maximum, 15 minutes, because a
  # first sign-up also verifies the email and sets up the authenticator app.
  auth_session_validity  = 15
  id_token_validity      = 5
  access_token_validity  = 5
  refresh_token_validity = 60

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "minutes"
  }
}

resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.main.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}

resource "aws_ssm_parameter" "oidc" {
  #checkov:skip=CKV2_AWS_34:Not secret: the issuer, client ID, sign-in domain and app origin all appear in the browser during sign-in. The client secret is the SecureString below.
  name = var.oidc_parameter
  type = "String"
  value = jsonencode({
    issuer     = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
    client_id  = aws_cognito_user_pool_client.web.id
    domain     = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
    app_origin = local.app_origin
  })
}

resource "aws_ssm_parameter" "oidc_client_secret" {
  name  = var.oidc_secret_parameter
  type  = "SecureString"
  value = aws_cognito_user_pool_client.web.client_secret
}
