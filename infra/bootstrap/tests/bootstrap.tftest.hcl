mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  github_owner = "example"
  budget_email = "owner@example.com"
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

run "ci_roles_trust_only_this_repository" {
  command = apply

  assert {
    condition     = strcontains(aws_iam_role.gha_plan.assume_role_policy, "repo:example/nettriage:pull_request")
    error_message = "The plan role must trust pull requests of this repo only."
  }
  assert {
    condition     = strcontains(aws_iam_role.gha_deploy_dev.assume_role_policy, "repo:example/nettriage:environment:dev")
    error_message = "The dev deploy role must trust the dev environment only."
  }
  assert {
    condition     = !strcontains(aws_iam_role.gha_deploy_dev.assume_role_policy, "*")
    error_message = "Trust policies must not use wildcards."
  }
}

run "roles_cannot_read_secrets_or_touch_the_account" {
  command = apply

  assert {
    condition     = strcontains(aws_iam_role_policy.gha_plan_no_secrets.policy, "kms:Decrypt")
    error_message = "The plan role must be denied secret decryption."
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.gha_deploy_dev_scope.policy, "organizations:*")
    error_message = "The deploy role must be denied Organizations actions (joining would forfeit credits)."
  }
}

run "budget_alerts_at_one_and_three_dollars" {
  command = apply

  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 3
    error_message = "Expected alerts at $1 actual, $3 actual and $3 forecast."
  }
}
