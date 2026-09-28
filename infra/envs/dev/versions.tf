terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # bucket, key, region and use_lockfile are passed with -backend-config by tools/deploy.
  backend "s3" {}
}

provider "aws" {
  region = "eu-north-1"

  default_tags {
    tags = {
      Project   = "nettriage"
      Env       = "dev"
      ManagedBy = "terraform"
    }
  }
}
