# The $5 backstop (spec §6.7): when the month's actual usage reaches $5, AWS Budgets attaches a
# policy that denies Bedrock to the triage workers. Budgets refreshes up to three times a day,
# so this lags by hours; the AI budgets in DynamoDB are the real-time limit (spec §6.6). To undo
# it, detach the policy from the role (runbook Part C, "Bedrock was cut off at $5").

resource "aws_iam_policy" "deny_bedrock" {
  name        = "nettriage-deny-bedrock"
  description = "Attached by the $5 budget action: no Bedrock calls until the owner detaches it."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "MonthlyBudgetSpent"
      Effect   = "Deny"
      Action   = "bedrock:InvokeModel*"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "budget_action" {
  name = "nettriage-budget-action"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "budget_action" {
  name = "attach-deny-bedrock"
  role = aws_iam_role.budget_action.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
      Resource  = [for role in var.triage_roles : "arn:aws:iam::${local.account_id}:role/${role}"]
      Condition = { ArnEquals = { "iam:PolicyARN" = aws_iam_policy.deny_bedrock.arn } }
    }]
  })
}

resource "aws_budgets_budget_action" "deny_bedrock" {
  budget_name        = aws_budgets_budget.monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "ABSOLUTE_VALUE"
    action_threshold_value = 5
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_bedrock.arn
      roles      = var.triage_roles
    }
  }

  subscriber {
    address           = var.budget_email
    subscription_type = "EMAIL"
  }

  depends_on = [aws_iam_role_policy.budget_action]
}
