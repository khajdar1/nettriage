mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  # The dev stage's triage worker exists (runbook A8 runs after Plan 5b's deploy).
  mock_data "aws_iam_roles" {
    defaults = {
      names = ["nettriage-dev-triage"]
    }
  }
  # Computed ARNs are short random strings by default; the budget action checks the ones it is
  # given look like ARNs.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-budget-action"
    }
  }
  mock_resource "aws_iam_policy" {
    defaults = {
      arn = "arn:aws:iam::123456789012:policy/nettriage-deny-bedrock"
    }
  }
}

variables {
  budget_email = "owner@example.com"
}

run "defaults_to_the_accounts_region" {
  command = plan

  assert {
    condition     = var.aws_region == "eu-north-1"
    error_message = "Regional resources live in eu-north-1, the account's only Region (spec Revision 2, R1)."
  }
}

run "state_bucket_is_private_versioned_encrypted_and_tls_only" {
  command = apply

  assert {
    condition     = aws_s3_bucket.state.bucket == "nettriage-tfstate-123456789012"
    error_message = "The state bucket name must include the account ID."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "The state bucket must block public access."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State history must be versioned."
  }
  assert {
    condition     = strcontains(aws_s3_bucket_policy.state.policy, "aws:SecureTransport")
    error_message = "The state bucket must deny non-TLS requests."
  }
}

run "budget_alerts_at_one_and_three_dollars" {
  command = apply

  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 4
    error_message = "Expected alerts at $1 and $3, actual and forecast (spec §6.7)."
  }
}

run "the_budget_counts_usage_before_credits" {
  command = apply

  assert {
    condition     = one(aws_budgets_budget.monthly.cost_types).include_credit == false
    error_message = "Credits would net the month's usage to $0, and no alert or action would ever fire on the Free plan (spec §6.7, Plan 5b)."
  }
}

run "at_five_dollars_bedrock_is_denied_to_the_triage_workers" {
  command = apply

  assert {
    condition     = aws_budgets_budget_action.deny_bedrock[0].budget_name == "nettriage-monthly" && aws_budgets_budget_action.deny_bedrock[0].notification_type == "ACTUAL"
    error_message = "The action watches the monthly budget's actual spend (spec §6.7)."
  }
  assert {
    condition     = one(aws_budgets_budget_action.deny_bedrock[0].action_threshold).action_threshold_type == "ABSOLUTE_VALUE" && one(aws_budgets_budget_action.deny_bedrock[0].action_threshold).action_threshold_value == 5
    error_message = "It acts at $5 (spec §6.7)."
  }
  assert {
    condition     = aws_budgets_budget_action.deny_bedrock[0].action_type == "APPLY_IAM_POLICY" && aws_budgets_budget_action.deny_bedrock[0].approval_model == "AUTOMATIC"
    error_message = "It attaches a policy, without waiting for approval."
  }
  assert {
    condition     = one(one(aws_budgets_budget_action.deny_bedrock[0].definition).iam_action_definition).roles == toset(["nettriage-dev-triage"])
    error_message = "It denies the triage workers that exist: dev's now, prod's when it exists."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.deny_bedrock.policy).Statement[0].Effect == "Deny" && jsondecode(aws_iam_policy.deny_bedrock.policy).Statement[0].Action == "bedrock:InvokeModel*"
    error_message = "The policy denies every Bedrock invocation (spec §6.7)."
  }
  assert {
    condition     = one(aws_budgets_budget_action.deny_bedrock[0].subscriber).address == "owner@example.com"
    error_message = "The owner is told when the action runs."
  }
}

run "budgets_may_attach_only_the_deny_policy_to_only_the_triage_workers" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role.budget_action[0].assume_role_policy).Statement[0].Principal.Service == "budgets.amazonaws.com" && jsondecode(aws_iam_role.budget_action[0].assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Only AWS Budgets, for this account, may assume the action's role."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.budget_action[0].policy).Statement[0].Action) == toset(["iam:AttachRolePolicy", "iam:DetachRolePolicy"])
    error_message = "The role may attach and detach role policies, nothing else."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.budget_action[0].policy).Statement[0].Resource) == toset(["arn:aws:iam::123456789012:role/nettriage-dev-triage"])
    error_message = "Only to the triage workers' roles."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.budget_action[0].policy).Statement[0].Condition.ArnEquals["iam:PolicyARN"] == "arn:aws:iam::123456789012:policy/nettriage-deny-bedrock"
    error_message = "And only the deny-Bedrock policy."
  }
}

run "before_any_triage_worker_exists_the_cutoff_waits" {
  command = apply

  override_data {
    target = data.aws_iam_roles.triage
    values = {
      names = []
    }
  }

  assert {
    condition     = length(aws_budgets_budget_action.deny_bedrock) == 0 && length(aws_iam_role.budget_action) == 0 && length(aws_iam_role_policy.budget_action) == 0
    error_message = "A first bootstrap (runbook A4) runs before any stage: the action would name a role that doesn't exist, so it waits for A8."
  }
  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 4
    error_message = "The budget and its alerts are still created."
  }
}
