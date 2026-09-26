# Ceiling for every role the dev deploy creates: no IAM, Organizations, account, billing, budgets or Cost Explorer actions, however that role's own policy is written.
# This is a permissions BOUNDARY, not a grant: it never grants anything by itself, it only caps what a role's own
# identity policy may request (ruling R20, spec §6.8 least privilege). Checkov's IAM-privilege checks are written for
# identity/resource policies that actually grant access; applied to a boundary they flag the broad NotAction/"*" ceiling
# that the boundary design requires. Narrowing it would either break the ceiling (denying actions the deploy role
# legitimately needs) or just move the same "Allow *" shape into the boundary's own guardrail statement.
resource "aws_iam_policy" "dev_boundary" {
  #checkov:skip=CKV_AWS_286:Permissions boundary, not a grant (ruling R20); the boundary's NotAction ceiling is itself the guardrail that closes IAM privilege escalation, ADR-verified in the app module's tests.
  #checkov:skip=CKV_AWS_287:Permissions boundary, not a grant (ruling R20); credentials exposure is bounded by whatever the role's own attached policy actually grants.
  #checkov:skip=CKV_AWS_288:Permissions boundary, not a grant (ruling R20); data exfiltration is bounded by the role's own attached policy, not this ceiling.
  #checkov:skip=CKV_AWS_289:Permissions boundary, not a grant (ruling R20); the boundary's job is to deny IAM/org/account/billing/budgets/CE, not to add per-resource constraints on everything else.
  #checkov:skip=CKV_AWS_290:Permissions boundary, not a grant (ruling R20); write access is bounded by the role's own attached policy.
  #checkov:skip=CKV_AWS_355:Permissions boundary (ruling R20); it must cover Resource "*" to cap any action a role's own policy might grant across all resources.
  name = "nettriage-dev-boundary"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      NotAction = ["iam:*", "organizations:*", "account:*", "billing:*", "budgets:*", "ce:*"]
      Resource  = "*"
    }]
  })
}
