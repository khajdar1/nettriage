resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_subjects = {
    plan       = "repo:${local.repo}:pull_request"
    deploy_dev = "repo:${local.repo}:environment:dev"
  }

  oidc_trust = {
    for name, subject in local.oidc_subjects : name => jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect    = "Allow"
        Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = subject
          }
        }
      }]
    })
  }
}

# Pull-request plans: read-only, and never able to decrypt secrets.
resource "aws_iam_role" "gha_plan" {
  name               = "nettriage-gha-plan"
  assume_role_policy = local.oidc_trust["plan"]
}

resource "aws_iam_role_policy_attachment" "gha_plan_readonly" {
  role       = aws_iam_role.gha_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "gha_plan_no_secrets" {
  name = "deny-secret-reads"
  role = aws_iam_role.gha_plan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Deny"
      Action   = ["kms:Decrypt", "secretsmanager:GetSecretValue"]
      Resource = "*"
    }]
  })
}

# Dev deploys: PowerUser, plus IAM limited to nettriage-dev-* roles. Any role this
# identity creates or grants a policy to must carry the nettriage-dev-boundary
# permissions boundary, so it can never escalate to full IAM, Organizations, account,
# billing or budget access via a role of its own making.
# Tightened with IAM Access Analyzer policy generation in Plan 7.
resource "aws_iam_role" "gha_deploy_dev" {
  name               = "nettriage-gha-deploy-dev"
  assume_role_policy = local.oidc_trust["deploy_dev"]
}

resource "aws_iam_role_policy_attachment" "gha_deploy_dev_power_user" {
  role       = aws_iam_role.gha_deploy_dev.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

resource "aws_iam_role_policy" "gha_deploy_dev_scope" {
  name = "stage-roles-and-guardrails"
  role = aws_iam_role.gha_deploy_dev.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageStageRoles"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole",
          "iam:TagRole", "iam:UntagRole", "iam:UpdateAssumeRolePolicy",
          "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies",
          "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
        ]
        Resource = "arn:aws:iam::${local.account_id}:role/nettriage-dev-*"
      },
      {
        Sid    = "GrantOnlyWithinBoundary"
        Effect = "Allow"
        Action = [
          "iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy",
          "iam:PutRolePermissionsBoundary",
        ]
        Resource  = "arn:aws:iam::${local.account_id}:role/nettriage-dev-*"
        Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.dev_boundary.arn } }
      },
      {
        Sid       = "PassStageRolesToLambda"
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = "arn:aws:iam::${local.account_id}:role/nettriage-dev-*"
        Condition = { StringEquals = { "iam:PassedToService" = "lambda.amazonaws.com" } }
      },
      {
        Sid      = "NoAccountOrgOrBillingChanges"
        Effect   = "Deny"
        Action   = ["organizations:*", "account:*", "billing:*", "budgets:*", "ce:*"]
        Resource = "*"
      },
    ]
  })
}
