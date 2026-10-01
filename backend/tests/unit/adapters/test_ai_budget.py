"""AI budgets (spec §6.6) in DynamoDB (moto): reserve, settle, release, and fail closed."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from conftest import FakeClock, RuntimeTable

from nettriage.adapters.ai_budget import (
    AiBudget,
    BudgetExhausted,
    BudgetUnavailable,
)


@pytest.fixture
def budget(runtime_table: RuntimeTable, clock: FakeClock) -> AiBudget:
    return AiBudget(runtime_table.client, runtime_table.name, clock)


def item(runtime_table: RuntimeTable, key: str) -> dict[str, Decimal]:
    """The item's numbers. DynamoDB stores numbers exactly; moto may write `0.000` for 0."""
    found = runtime_table.client.get_item(TableName=runtime_table.name, Key={"pk": {"S": key}})
    attributes: dict[str, Any] = found.get("Item", {})
    return {name: Decimal(value["N"]) for name, value in attributes.items() if "N" in value}


def test_a_reservation_is_counted_against_both_budgets_with_an_expiry(
    budget: AiBudget, runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    org = uuid4()

    reservation = budget.reserve(org, 1_700, Decimal("0.003"))

    assert reservation.org_key == f"BUDGET#{org}#2026-09-28"
    assert reservation.global_key == "GBUDGET#2026-09-28"
    org_item = item(runtime_table, reservation.org_key)
    assert (org_item["tokens_reserved"], org_item["tokens_used"]) == (Decimal(1_700), Decimal(0))
    assert int(org_item["expires_at"]) == int((clock() + timedelta(days=2)).timestamp())
    assert item(runtime_table, reservation.global_key)["usd_reserved"] == Decimal("0.003")


def test_settling_replaces_the_reservation_with_the_real_usage(
    budget: AiBudget, runtime_table: RuntimeTable
) -> None:
    reservation = budget.reserve(uuid4(), 1_700, Decimal("0.003"))

    budget.settle(reservation, 1_200, Decimal("0.0021"))

    org_item = item(runtime_table, reservation.org_key)
    assert org_item["tokens_reserved"] == org_item["tokens_used"] == Decimal(1_200)
    global_item = item(runtime_table, reservation.global_key)
    assert global_item["usd_reserved"] == global_item["usd_used"] == Decimal("0.0021")


def test_releasing_gives_the_reservation_back(
    budget: AiBudget, runtime_table: RuntimeTable
) -> None:
    reservation = budget.reserve(uuid4(), 1_700, Decimal("0.003"))

    budget.release(reservation)

    assert item(runtime_table, reservation.org_key)["tokens_reserved"] == 0
    assert item(runtime_table, reservation.global_key)["usd_reserved"] == 0


def test_an_org_can_not_reserve_past_its_daily_tokens(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    budget = AiBudget(runtime_table.client, runtime_table.name, clock, org_daily_tokens=3_000)
    org = uuid4()
    first = budget.reserve(org, 2_000, Decimal("0.001"))

    with pytest.raises(BudgetExhausted) as exhausted:
        budget.reserve(org, 1_500, Decimal("0.001"))

    assert exhausted.value.scope == "org"
    assert item(runtime_table, first.org_key)["tokens_reserved"] == 2_000
    assert budget.reserve(uuid4(), 2_000, Decimal("0.001"))  # another org has its own budget


def test_the_spend_cap_covers_all_orgs_and_gives_back_the_orgs_tokens(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    budget = AiBudget(
        runtime_table.client, runtime_table.name, clock, global_daily_usd=Decimal("0.005")
    )
    budget.reserve(uuid4(), 1_000, Decimal("0.004"))
    org = uuid4()

    with pytest.raises(BudgetExhausted) as exhausted:
        budget.reserve(org, 1_000, Decimal("0.002"))

    assert exhausted.value.scope == "global"
    assert item(runtime_table, f"BUDGET#{org}#2026-09-28")["tokens_reserved"] == 0


def test_a_call_bigger_than_the_whole_budget_is_refused_without_a_write(
    budget: AiBudget, runtime_table: RuntimeTable
) -> None:
    org = uuid4()

    with pytest.raises(BudgetExhausted):
        budget.reserve(org, 100_001, Decimal("0.001"))

    assert item(runtime_table, f"BUDGET#{org}#2026-09-28") == {}


def test_a_new_day_starts_with_a_fresh_budget(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    budget = AiBudget(runtime_table.client, runtime_table.name, clock, org_daily_tokens=3_000)
    org = uuid4()
    budget.reserve(org, 3_000, Decimal("0.001"))
    clock.advance(timedelta(days=1))

    assert budget.reserve(org, 3_000, Decimal("0.001")).org_key == f"BUDGET#{org}#2026-09-29"


def test_a_budget_that_can_not_be_read_fails_closed(
    runtime_table: RuntimeTable, clock: FakeClock
) -> None:
    budget = AiBudget(runtime_table.client, "no-such-table", clock)

    with pytest.raises(BudgetUnavailable):
        budget.reserve(uuid4(), 1_000, Decimal("0.001"))
