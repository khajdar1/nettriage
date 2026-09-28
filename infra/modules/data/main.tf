# The DynamoDB `runtime` table (spec §5.5): sessions, sign-in state and rate-limit keys, all
# expiring through TTL. Provisioned capacity stays inside the Always Free 25 read and 25 write
# units: prod gets 10 of each, dev 3.
locals {
  capacity = var.stage == "prod" ? 10 : 3
}

resource "aws_dynamodb_table" "runtime" {
  name                        = "nettriage-${var.stage}-runtime"
  billing_mode                = "PROVISIONED"
  read_capacity               = local.capacity
  write_capacity              = local.capacity
  hash_key                    = "pk"
  deletion_protection_enabled = var.stage == "prod"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
