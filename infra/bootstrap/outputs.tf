output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "gha_plan_role_arn" {
  value = aws_iam_role.gha_plan.arn
}

output "gha_deploy_dev_role_arn" {
  value = aws_iam_role.gha_deploy_dev.arn
}

output "dev_boundary_policy_arn" {
  value = aws_iam_policy.dev_boundary.arn
}
