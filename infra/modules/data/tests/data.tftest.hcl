mock_provider "aws" {}

variables {
  stage = "dev"
}

run "runtime_table_matches_the_spec" {
  command = apply

  assert {
    condition     = aws_dynamodb_table.runtime.name == "nettriage-dev-runtime"
    error_message = "Names follow nettriage-<stage>-<name>."
  }
  assert {
    condition     = aws_dynamodb_table.runtime.hash_key == "pk" && one(aws_dynamodb_table.runtime.attribute).type == "S"
    error_message = "The partition key is the string pk (spec §5.5)."
  }
  assert {
    condition     = one(aws_dynamodb_table.runtime.ttl).attribute_name == "expires_at" && one(aws_dynamodb_table.runtime.ttl).enabled
    error_message = "Items expire through TTL on expires_at (spec §5.5)."
  }
}

run "dev_capacity_stays_inside_always_free" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.runtime.billing_mode == "PROVISIONED" && aws_dynamodb_table.runtime.read_capacity == 10 && aws_dynamodb_table.runtime.write_capacity == 10
    error_message = "dev gets 10 RCU and 10 WCU, provisioned: with prod's 10, 20 of the 25 Always Free units (spec §5.5)."
  }
  assert {
    condition     = !aws_dynamodb_table.runtime.deletion_protection_enabled
    error_message = "dev can be torn down."
  }
}

run "prod_gets_more_capacity_and_deletion_protection" {
  command = plan

  variables {
    stage = "prod"
  }

  assert {
    condition     = aws_dynamodb_table.runtime.read_capacity == 10 && aws_dynamodb_table.runtime.write_capacity == 10
    error_message = "prod gets 10 RCU and 10 WCU (spec §5.5)."
  }
  assert {
    condition     = aws_dynamodb_table.runtime.deletion_protection_enabled
    error_message = "prod's table is protected from deletion."
  }
}
