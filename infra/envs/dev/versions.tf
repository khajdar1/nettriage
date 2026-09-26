terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # bucket, key, region and use_lockfile are passed with -backend-config (see Task 12).
  backend "s3" {}
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "nettriage"
      Env       = "dev"
      ManagedBy = "terraform"
    }
  }
}
