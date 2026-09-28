mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "eu-north-1"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  stage                 = "dev"
  app_domain            = "d111111abcdef8.cloudfront.net"
  oidc_parameter        = "/nettriage/dev/api/oidc"
  oidc_secret_parameter = "/nettriage/dev/api/oidc-client-secret"
}

run "mfa_is_mandatory_and_totp_only" {
  command = apply

  assert {
    condition     = aws_cognito_user_pool.main.mfa_configuration == "ON" && one(aws_cognito_user_pool.main.software_token_mfa_configuration).enabled
    error_message = "MFA is required, TOTP only (spec §6.1)."
  }
  assert {
    condition     = length(aws_cognito_user_pool.main.sms_configuration) == 0
    error_message = "There is no SMS option (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool.main.user_pool_tier == "ESSENTIALS"
    error_message = "The pool is on the Essentials plan (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool.main.username_attributes == toset(["email"]) && aws_cognito_user_pool.main.auto_verified_attributes == toset(["email"])
    error_message = "The username is the email address, and it is verified (spec §6.1)."
  }
  assert {
    condition     = one(aws_cognito_user_pool.main.password_policy).minimum_length == 12
    error_message = "Passwords are at least 12 characters (spec §6.1)."
  }
}

run "the_app_client_is_confidential_and_code_flow_only" {
  command = apply

  assert {
    condition     = aws_cognito_user_pool_client.web.generate_secret && aws_cognito_user_pool_client.web.allowed_oauth_flows == toset(["code"])
    error_message = "The client is confidential and uses the authorization-code grant only (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool_client.web.allowed_oauth_scopes == toset(["openid", "email", "profile"])
    error_message = "The client asks for openid, email and profile (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool_client.web.callback_urls == toset(["https://d111111abcdef8.cloudfront.net/api/auth/callback"]) && aws_cognito_user_pool_client.web.logout_urls == toset(["https://d111111abcdef8.cloudfront.net/"])
    error_message = "Only the stage's exact callback and logout URLs are allowed (spec §6.1)."
  }
  assert {
    condition     = aws_cognito_user_pool_client.web.prevent_user_existence_errors == "ENABLED"
    error_message = "PreventUserExistenceErrors is on (spec §6.1)."
  }
}

run "the_api_reads_the_clients_settings_and_secret_from_ssm" {
  command = apply

  assert {
    condition     = aws_ssm_parameter.oidc.type == "String" && aws_ssm_parameter.oidc_client_secret.type == "SecureString"
    error_message = "The settings are plain; the client secret is a SecureString (spec §3.2)."
  }
  assert {
    condition     = keys(jsondecode(aws_ssm_parameter.oidc.value)) == ["app_origin", "client_id", "domain", "issuer"]
    error_message = "The settings JSON has exactly the keys the API reads (nettriage.entrypoints.api.wiring)."
  }
  assert {
    condition     = jsondecode(aws_ssm_parameter.oidc.value).app_origin == "https://d111111abcdef8.cloudfront.net"
    error_message = "The app origin is the CloudFront domain."
  }
  assert {
    condition     = startswith(jsondecode(aws_ssm_parameter.oidc.value).issuer, "https://cognito-idp.eu-north-1.amazonaws.com/")
    error_message = "The issuer is the pool's Cognito URL in eu-north-1."
  }
}

run "the_sign_in_domain_does_not_expose_the_account_id" {
  command = apply

  assert {
    condition     = startswith(aws_cognito_user_pool_domain.main.domain, "nettriage-dev-") && !strcontains(aws_cognito_user_pool_domain.main.domain, "123456789012")
    error_message = "The prefix domain is nettriage-<stage>-<hash>, without the account ID."
  }
  assert {
    condition     = aws_cognito_user_pool_domain.main.managed_login_version == 2
    error_message = "Sign-in uses Cognito's managed login (spec §2.2)."
  }
}
