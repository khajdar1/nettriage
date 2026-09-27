# The bootstrap's own state lives in the bucket it creates. `just bootstrap` applies the first
# run with local state, then pushes it here; bucket, key and region come from tools/deploy.
terraform {
  backend "s3" {}
}
