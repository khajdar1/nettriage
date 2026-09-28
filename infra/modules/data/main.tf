# The DynamoDB `runtime` table (spec §5.5): sessions, sign-in state and rate-limit keys, all
# expiring through TTL. Provisioned capacity stays inside the Always Free 25 read and 25 write
# units: each stage gets 10 of each (the owner raised dev from 3 in Plan 3b, because one looping
# client could use up 3 write units a second and break sign-in for everyone).
locals {
  capacity = 10
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
