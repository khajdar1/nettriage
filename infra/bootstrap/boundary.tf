# Ceiling for every role the dev deploy creates: no IAM, Organizations, account, billing, budgets or Cost Explorer actions, however that role's own policy is written.
resource "aws_iam_policy" "dev_boundary" {
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
