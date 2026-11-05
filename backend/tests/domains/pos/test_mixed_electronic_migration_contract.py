"""Offline preservation checks for versioned SQL transformations; not a DB rehearsal."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"


def migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("contract_" + name, VERSIONS / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION public\.{re.escape(name)}\(.*?\$\$;",
        sql,
        flags=re.DOTALL,
    )
    assert match is not None, name
    return match.group()


@pytest.mark.parametrize("index", range(4))
def test_private_electronic_helper_rewrites_are_exactly_reversible(index: int) -> None:
    current = migration("0219_mixed_electronic_checkout.py")
    historical = migration("0212_unit_electronic_checkout.py")
    signature = current.GENERIC[index]
    original = function(historical.SQL, signature.split("(")[0])
    updated = current._generic_definition(signature, original, upgrade=True)
    if signature.startswith("unit_electronic_write_allowed("):
        assert updated.count("public.pos_physical_checkout") == 1
        assert "AND NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout marker" in updated
    else:
        assert "public.pos_physical_checkout" not in updated
    assert "public.unit_electronic_checkout_marker" in updated
    assert current._generic_definition(signature, updated, upgrade=False) == original


def test_new_mixed_assert_retains_legacy_and_physical_proofs() -> None:
    current = migration("0219_mixed_electronic_checkout.py")
    original = function(
        migration("0188_mixed_cash_checkout.py").FUNCTIONS_SQL, "assert_mixed_checkout"
    )
    updated = current._clone_assert(original)
    for proof in (
        "mixed_checkout_legacy_stock_invalid",
        "mod(v_floor,2)=1",
        "mixed_checkout_physical_line_invalid",
        "mixed_checkout_receipt_invalid",
        "mixed_checkout_time_invalid",
        "mixed_checkout_name_invalid",
        "m.receipt_payload",
        "i.quote_payload",
        "p.payment_attempt_id",
    ):
        if proof == "p.payment_attempt_id":
            # Canonical payment/attempt proof is delegated, never discarded.
            assert (
                "PERFORM public.assert_unit_electronic_payments(p_tenant,p_sale,p_final)" in updated
            )
        else:
            assert proof in original and proof in updated
    assert "public.pos_unit_cash_intent" not in updated
    assert "public.pos_unit_payment_plan_intent" in updated
    assert "jsonb_array_length(v_payment)" in updated
    assert "jsonb_build_array(v_payment)" not in updated


def historical_patch_bodies() -> dict[str, str]:
    cash = migration("0188_mixed_cash_checkout.py")
    binding = migration("0209_unit_payment_binding.py")
    components = migration("0210_unit_checkout_payment_components.py")
    electronic = migration("0212_unit_electronic_checkout.py")
    bodies = {
        "trg_unit_payment_attempt_binding_guard()": function(
            electronic.SQL, "trg_unit_payment_attempt_binding_guard"
        ),
        "trg_unit_payment_binding_fence()": function(binding.SQL, "trg_unit_payment_binding_fence"),
        "assert_mixed_checkout(uuid,uuid,boolean)": function(
            cash.FUNCTIONS_SQL, "assert_mixed_checkout"
        ),
        "trg_mixed_stock_checkout_binding()": function(
            cash.FUNCTIONS_SQL, "trg_mixed_stock_checkout_binding"
        ),
        "assert_unit_checkout_payment_components(uuid,uuid,boolean)": function(
            components.SQL, "assert_unit_checkout_payment_components"
        ),
        "trg_unit_checkout_payment_component_guard()": function(
            components.SQL, "trg_unit_checkout_payment_component_guard"
        ),
        "trg_unit_cart_payment_boundary()": electronic.BOUNDARY_DISPATCH,
    }
    for signature, old, new in electronic.PATCHES:
        if signature in bodies and old in bodies[signature]:
            bodies[signature] = bodies[signature].replace(old, new, 1)
    return bodies


def test_all_in_place_patches_restore_original_identity_and_guards() -> None:
    current = migration("0219_mixed_electronic_checkout.py")
    bodies = historical_patch_bodies()
    originals = dict(bodies)
    for signature, old, new, count in current.PATCHES:
        bodies[signature] = current._change(bodies[signature], old, new, count=count)
    for signature, old, new, count in reversed(current.PATCHES):
        bodies[signature] = current._change(bodies[signature], new, old, count=count)
    assert bodies == originals


def test_unexpected_historical_definition_is_rejected_without_guessing() -> None:
    current = migration("0219_mixed_electronic_checkout.py")
    with pytest.raises(RuntimeError, match="historical guard changed"):
        current._change("SELECT 1", "absent anchor", "new clause")


def test_private_marker_projection_is_exclusive_and_has_no_app_grant() -> None:
    sql = migration("0219_mixed_electronic_checkout.py").MARKERS_SQL
    assert "security_barrier=true" in sql
    assert sql.count("payment_journal_version=3") == 2
    assert sql.count("NOT EXISTS") == 2
    assert "false AS mixed" in sql and "true AS mixed" in sql
    assert "OWNER TO aurum_schema_owner" in sql
    assert "REVOKE ALL" in sql and "PUBLIC,aurum_app,aurum_support" in sql
    assert "GRANT" not in sql
