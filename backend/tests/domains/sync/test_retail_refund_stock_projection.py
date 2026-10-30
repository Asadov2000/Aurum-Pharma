"""Trusted paid refunds advance history while available physical stock stays unchanged."""

from typing import Literal

import pytest

from app.core.errors import BusinessRuleError
from app.domains.pos.retail_refund_events import (
    RetailRefundEventResult,
    RetailRefundSourceKind,
    build_retail_refund_event,
    retail_refund_event_fingerprint,
)
from app.domains.pos.retail_sale_event_storage import RetailSaleEventAnchor
from app.domains.pos.retail_sale_events import RetailSaleStreamPosition
from app.domains.sync.models import SyncOutboxEvent
from app.domains.sync.physical_stock_projection import reduce_physical_stock_projection
from app.domains.sync.service import _require_compatible_delivery
from tests.domains.pos.test_retail_refund_events import KINDS, event, payload
from tests.domains.sync.test_physical_stock_projection import baseline, checkpoint


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("replay", [False, True])
def test_refund_quarantine_never_replenishes_available_stock(
    kind: RetailRefundSourceKind, replay: bool
) -> None:
    anchor = checkpoint()
    records, configuration = baseline()
    refund = payload(kind).model_dump(mode="json")
    for field in ("result", "quarantine"):
        refund[field]["tenant_id"] = str(anchor.tenant_id)
        refund[field]["branch_id"] = str(anchor.branch_id)
    event = build_retail_refund_event(
        RetailRefundEventResult.model_validate(refund),
        position=RetailSaleStreamPosition(
            origin_node_id=anchor.origin_node_id,
            writer_epoch=anchor.writer_epoch,
            sequence=anchor.sequence + 1,
            previous_source_checksum=anchor.source_checksum,
            previous_projection_checksum=anchor.projection_checksum,
        ),
    )
    trusted = RetailSaleEventAnchor(
        tenant_id=event.tenant_id,
        branch_id=event.branch_id,
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
        sequence=event.sequence,
        previous_source_checksum=anchor.source_checksum,
        previous_projection_checksum=anchor.projection_checksum,
        event_fingerprint=retail_refund_event_fingerprint(event),
    )
    result = reduce_physical_stock_projection(
        baseline_records=records,
        baseline_configuration=configuration,
        anchor=anchor,
        events=(event, event) if replay else (event,),
        stock_anchors={},
        retail_anchors={},
        refund_anchors={event.event_id: trusted},
    )
    assert result.ready
    assert result.physical_records == records and result.configuration == configuration
    assert result.last_sequence == event.sequence
    assert result.source_checksum == event.stream_checksum
    assert result.projection_checksum == event.projection_checksum
    assert result.pending_checkout_sale_ids == ()


@pytest.mark.parametrize("fault", ["missing", "scope", "fingerprint", "checkpoint"])
def test_refund_requires_independent_exact_scope_fingerprint_and_checkpoint(
    fault: Literal["missing", "scope", "fingerprint", "checkpoint"],
) -> None:
    original = checkpoint()
    records, configuration = baseline()
    data = payload().model_dump(mode="json")
    for field in ("result", "quarantine"):
        data[field]["tenant_id"] = str(original.tenant_id)
        data[field]["branch_id"] = str(original.branch_id)
    incoming = build_retail_refund_event(
        RetailRefundEventResult.model_validate(data),
        position=RetailSaleStreamPosition(
            origin_node_id=original.origin_node_id,
            writer_epoch=original.writer_epoch,
            sequence=original.sequence + 1,
            previous_source_checksum=original.source_checksum,
            previous_projection_checksum=original.projection_checksum,
        ),
    )
    trusted = RetailSaleEventAnchor(
        tenant_id=incoming.tenant_id,
        branch_id=incoming.branch_id,
        origin_node_id=incoming.origin_node_id,
        writer_epoch=incoming.writer_epoch,
        sequence=incoming.sequence,
        previous_source_checksum=original.source_checksum,
        previous_projection_checksum=original.projection_checksum,
        event_fingerprint=retail_refund_event_fingerprint(incoming),
    )
    changes: dict[str, object] = {}
    if fault == "scope":
        changes["sequence"] = incoming.sequence + 1
    elif fault == "fingerprint":
        changes["event_fingerprint"] = "f" * 64
    elif fault == "checkpoint":
        changes["previous_source_checksum"] = "f" * 64
    trusted = trusted.model_copy(update=changes)
    with pytest.raises(ValueError, match="Refund|checkpoint"):
        reduce_physical_stock_projection(
            baseline_records=records,
            baseline_configuration=configuration,
            anchor=original,
            events=(incoming,),
            stock_anchors={},
            retail_anchors={},
            refund_anchors={} if fault == "missing" else {incoming.event_id: trusted},
        )


@pytest.mark.parametrize("kind", KINDS)
def test_new_refund_delivery_stays_closed_until_receiver_capability_is_enabled(
    kind: RetailRefundSourceKind,
) -> None:
    rows = [SyncOutboxEvent(**event(kind).model_dump(mode="python"))]
    with pytest.raises(BusinessRuleError, match="compatible receiver"):
        _require_compatible_delivery(rows)
