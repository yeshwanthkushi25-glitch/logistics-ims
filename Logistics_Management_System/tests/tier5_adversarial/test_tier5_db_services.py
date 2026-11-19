"""
Logistics IMS - Tier 5 Database & Services Adversarial Verification Suite.

Milestone 5 Phase 2: Adversarial Coverage Hardening.

Covers:
1. Extreme inventory allocation & restock:
   - Zero, negative quantities, and non-integer types (bool, float, str, None).
   - Exact depletion to zero available stock and over-reservation rejection.
   - Cycle count variance adjustments, floor violations, and negative on_hand rejection.
   - Multi-bin allocations, bin_code resolution, and non-existent bin handling.
   - Reorder point (ROP) mathematical boundaries and negative input rejection.
2. Multi-warehouse order splitting & atomicity rollback:
   - Single order requiring items sourced across 3+ distinct warehouses.
   - Independent shipment manifests generated per sourcing warehouse.
   - Atomicity rollback: partial reservation abort releasing holds across all sourcing warehouses.
   - Simulated payment failure abort confirming zero orphan records in DB and NoSQL.
   - Idempotency replay verification with identical order reproduction.
3. 7-state shipment FSM illegal transitions:
   - Terminal state immutability: DELIVERED cannot transition to any other state.
   - Terminal state immutability: RETURNED cannot transition to any other state.
   - Milestone recording rejected on DELIVERED and RETURNED shipments.
   - Illegal state leaps: skipping intermediate states (CREATED -> DELIVERED, etc.).
   - Rejection of invalid status strings.
4. Polymorphic telemetry:
   - Out-of-bounds geographic coordinates (lat > 90, lat < -90, lon > 180, lon < -180).
   - Malformed, non-dict payloads, and nested non-dict geo objects.
   - Telemetry on non-existent shipments.
   - Telemetry on terminal shipments: position coordinates remain immutable.
   - Sparse payloads and ultra-large diagnostic payloads.
5. Graph routing under dynamic network perturbation:
   - A* admissibility guard: Haversine distance heuristic strictly rejected for cost/time criteria.
   - Simultaneous multi-corridor outages causing total partition or alternative fallback rerouting.
   - Unreachable nodes, non-existent hubs, empty nodes, and self-loop 0-distance invariants.
   - Vehicle weight constraint corridor pruning.
6. Cache-aside invalidation lifecycle:
   - Capacity-bounded LRU eviction and access-order promotion.
   - TTL expiration and lazy pruning on read and boundary capacity checks.
   - Glob pattern invalidation across namespaces ('shipment:track:*', 'inventory:*').
   - Telemetry statistics, hit ratio calculation, and counter resets.
   - Cache coherence across shipment status updates, milestones, and telemetry writes.
7. Transaction atomicity & storage/NoSQL security:
   - Top-level transaction clean rollback on unhandled exceptions with zero persistence.
   - Nested savepoint isolation: inner rollback leaves outer transaction intact.
   - Database CHECK constraint enforcement and rollback.
   - LocalStorageClient directory traversal, absolute drive path, and reserved Windows device defenses.
   - NoSQL DocumentStore JSON path operator injection defenses.
"""

import os
import sys
import time
import tempfile
import sqlite3
import pytest
from typing import Generator, Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.db.connection import (
    create_connection,
    get_db_connection,
    close_all_connections,
    set_default_db_path,
    transaction_scope,
)
from backend.db.init_db import init_database
from backend.db.cache import CacheManager, get_cache_manager
from backend.db.nosql import DocumentStore, get_doc_store
from backend.db.storage import LocalStorageClient

from backend.services.inventory_service import (
    calculate_reorder_point,
    get_inventory_levels,
    allocate_inventory,
    release_inventory,
    restock_inventory,
    adjust_inventory,
    InsufficientStockException,
    ProductNotFoundException,
    InvalidInventoryOperationException,
)
from backend.services.fulfillment_service import (
    create_order_atomic,
    get_order,
    cancel_order,
    PaymentFailedException,
    OrderNotFoundException,
    OrderStateTransitionException,
)
from backend.services.shipment_service import (
    create_shipment,
    update_shipment_status,
    record_shipment_milestone,
    ingest_telemetry,
    get_tracking_details,
    get_shipment_telemetry,
    ShipmentNotFoundException,
    InvalidShipmentStatusException,
    ShipmentStateTransitionException,
    InvalidMilestoneException,
    InvalidTelemetryException,
)
from backend.services.routing_service import (
    calculate_optimal_route,
    simulate_edge_outage_and_reroute,
    seeded_network_cte_routing,
    RoutingException,
    NodeNotFoundException,
    NoPathFoundException,
    VehicleConstraintViolationException,
)


# =============================================================================
# Test Database Fixture
# =============================================================================

@pytest.fixture
def isolated_db() -> Generator[str, None, None]:
    """
    Creates an isolated temporary SQLite database populated with demo data.
    Ensures thread-safe connection cleanup and cache flush.
    """
    close_all_connections()
    fd, path = tempfile.mkstemp(suffix=".db", prefix="tier5_adv_")
    os.close(fd)
    set_default_db_path(path)

    init_database(seed_demo_data=True, db_path=path)
    close_all_connections()

    cache = get_cache_manager()
    cache.flush()
    cache.reset_stats()

    # Reset DocumentStore singleton
    import backend.db.nosql as nosql_mod
    nosql_mod._GLOBAL_DOC_STORE = None

    yield path

    close_all_connections()
    nosql_mod._GLOBAL_DOC_STORE = None
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


# =============================================================================
# 1. Extreme Inventory Allocation & Restock
# =============================================================================

def test_adversarial_inventory_allocation_zero_and_negative_quantities(isolated_db):
    """Attack: Pass zero, negative, and invalid values to inventory allocation and release."""
    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", 0)

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", -1)

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", -9999)

    with pytest.raises(ValueError, match="Release quantity must be a positive integer"):
        release_inventory("wh-01", "prod-01", 0)

    with pytest.raises(ValueError, match="Release quantity must be a positive integer"):
        release_inventory("wh-01", "prod-01", -5)


def test_adversarial_inventory_allocation_non_integer_types(isolated_db):
    """Attack: Pass non-integer types (float, string, bool, None, list) to allocation."""
    # In Python, isinstance(True, int) is True, so explicit bool check is required!
    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", 3.14)

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", "10")

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", True)

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", False)

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", None)

    with pytest.raises(ValueError, match="Reservation quantity must be a positive integer"):
        allocate_inventory("wh-01", "prod-01", [5])


def test_adversarial_inventory_restock_invalid_types_and_values(isolated_db):
    """Attack: Pass zero, negative, float, and bool values to restock and adjust operations."""
    with pytest.raises(ValueError, match="Restock quantity must be a positive integer"):
        restock_inventory("wh-01", "prod-01", 0)

    with pytest.raises(ValueError, match="Restock quantity must be a positive integer"):
        restock_inventory("wh-01", "prod-01", -10)

    with pytest.raises(ValueError, match="Restock quantity must be a positive integer"):
        restock_inventory("wh-01", "prod-01", 15.5)

    with pytest.raises(ValueError, match="Restock quantity must be a positive integer"):
        restock_inventory("wh-01", "prod-01", True)

    with pytest.raises(ValueError, match="Restock quantity must be a positive integer"):
        restock_inventory("wh-01", "prod-01", "50")

    # Adjust inventory non-zero integer check
    with pytest.raises(ValueError, match="Adjustment delta must be a non-zero integer"):
        adjust_inventory("wh-01", "prod-01", delta=0, reason="CYCLE_COUNT")

    with pytest.raises(ValueError, match="Adjustment delta must be a non-zero integer"):
        adjust_inventory("wh-01", "prod-01", delta=True, reason="CYCLE_COUNT")

    with pytest.raises(ValueError, match="Adjustment delta must be a non-zero integer"):
        adjust_inventory("wh-01", "prod-01", delta=2.5, reason="CYCLE_COUNT")


def test_adversarial_inventory_exact_depletion_to_zero_and_over_reservation(isolated_db):
    """Boundary: Allocate stock to exact zero depletion, then verify over-reservation fails."""
    levels = get_inventory_levels(warehouse_id="wh-01", product_id="prod-01")
    assert len(levels) >= 1
    initial_avail = levels[0]["quantity_available"]
    initial_on_hand = levels[0]["quantity_on_hand"]
    initial_reserved = levels[0]["quantity_reserved"]
    assert initial_avail > 0

    # 1. Allocate exactly the entire available quantity
    allocated = allocate_inventory("wh-01", "prod-01", initial_avail)
    assert allocated is True

    # 2. Verify stock invariant: available is now exactly 0, reserved == on_hand
    post_levels = get_inventory_levels(warehouse_id="wh-01", product_id="prod-01")
    assert post_levels[0]["quantity_available"] == 0
    assert post_levels[0]["quantity_reserved"] == initial_on_hand

    # 3. Attempting to allocate even 1 more unit MUST raise InsufficientStockException
    with pytest.raises(InsufficientStockException, match="Insufficient stock"):
        allocate_inventory("wh-01", "prod-01", 1)

    # 4. Release the exact allocated stock
    release_inventory("wh-01", "prod-01", initial_avail)
    restored_levels = get_inventory_levels(warehouse_id="wh-01", product_id="prod-01")
    assert restored_levels[0]["quantity_available"] == initial_avail
    assert restored_levels[0]["quantity_reserved"] == initial_reserved


def test_adversarial_inventory_cycle_count_depletion_and_negative_floor(isolated_db):
    """Boundary: Deplete on_hand to 0 via adjustment, then verify negative on_hand rejection."""
    # Ensure a dedicated test product in wh-01 with known on_hand=10, reserved=0
    conn = get_db_connection(isolated_db)
    test_prod_id = "prod-adv-deplete-01"
    conn.execute("""
        INSERT OR REPLACE INTO products (id, sku, name, category, unit_price, weight_kg, reorder_threshold)
        VALUES (?, 'SKU-DEPLETE-01', 'Deplete Test SKU', 'Testing', 10.0, 1.0, 5)
    """, (test_prod_id,))
    conn.execute("""
        INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved)
        VALUES ('inv-deplete-01', 'wh-01', ?, 10, 0)
    """, (test_prod_id,))

    # 1. Deplete on_hand exactly to 0
    res = adjust_inventory("wh-01", test_prod_id, delta=-10, reason="CYCLE_COUNT")
    assert res["new_on_hand"] == 0

    # 2. Attempting to adjust by -1 when on_hand is 0 must fail
    with pytest.raises((InvalidInventoryOperationException, InsufficientStockException)):
        adjust_inventory("wh-01", test_prod_id, delta=-1, reason="CYCLE_COUNT")

    # 3. Restock 10 units, reserve 6 units
    restock_inventory("wh-01", test_prod_id, quantity=10)
    allocate_inventory("wh-01", test_prod_id, quantity=6)

    # 4. Attempt to adjust delta=-5 (new on_hand=5 < reserved=6) -> violates reservation hold invariant
    with pytest.raises(InsufficientStockException, match="violates stock invariant"):
        adjust_inventory("wh-01", test_prod_id, delta=-5, reason="DAMAGED")


def test_adversarial_inventory_multi_bin_allocation_and_unknown_bin(isolated_db):
    """Boundary: Test restock with valid bin_code and gracefully handle non-existent bin_code."""
    # Restock with valid seeded bin code
    res1 = restock_inventory(
        warehouse_id="wh-01",
        product_id="prod-01",
        quantity=5,
        bin_code="WH-CHI-ZN-A-AI01-RK01-SH01-BN01"
    )
    assert res1["status"] == "RESTOCKED"

    # Restock with unknown/non-existent bin_code
    res2 = restock_inventory(
        warehouse_id="wh-01",
        product_id="prod-01",
        quantity=5,
        bin_code="NONEXISTENT-BIN-UNKNOWN-999"
    )
    assert res2["status"] == "RESTOCKED"

    # Verify audit log in NoSQL
    doc_store = get_doc_store(isolated_db)
    audit_docs = doc_store.find("audit_event_logs", {"warehouse_id": "wh-01", "product_id": "prod-01"})
    assert len(audit_docs) >= 2


def test_adversarial_reorder_point_calculation_boundaries():
    """Mathematical verification: Test Reorder Point calculation limits and input validation."""
    # Baseline: 0 demand, 0 lead time, 0 safety stock = 0
    assert calculate_reorder_point(0.0, 0.0, 0.0) == 0

    # Ceiling rounding: (1.2 * 3.1) + 0.5 = 3.72 + 0.5 = 4.22 -> ceil -> 5
    assert calculate_reorder_point(1.2, 3.1, 0.5) == 5

    # Negative parameter rejection
    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_reorder_point(-1.0, 5.0, 2.0)

    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_reorder_point(5.0, -1.0, 2.0)

    with pytest.raises(ValueError, match="must be non-negative"):
        calculate_reorder_point(5.0, 5.0, -0.5)


# =============================================================================
# 2. Multi-Warehouse Order Splitting & Atomicity Rollback
# =============================================================================

def test_adversarial_multi_warehouse_order_splitting_across_3_warehouses(isolated_db):
    """
    Complex workflow: Single order with 3 line items, each exclusively available in a different warehouse.
    Verifies:
    1. Order is confirmed.
    2. Items allocated across 3 distinct warehouses (wh-01, wh-02, wh-03).
    3. Exactly 3 distinct shipment manifest stubs generated (one per warehouse).
    4. Reservations held across all 3 warehouse ledgers.
    """
    conn = get_db_connection(isolated_db)

    # Setup 3 products uniquely stocked in 3 warehouses
    p1, p2, p3 = "prod-split-01", "prod-split-02", "prod-split-03"
    for p_id, sku in [(p1, "SKU-SP1"), (p2, "SKU-SP2"), (p3, "SKU-SP3")]:
        conn.execute("""
            INSERT OR REPLACE INTO products (id, sku, name, category, unit_price, weight_kg, reorder_threshold)
            VALUES (?, ?, 'Split Product', 'Multi-Warehouse', 25.0, 1.5, 2)
        """, (p_id, sku))

    # Product 1 only in wh-01, Product 2 only in wh-02, Product 3 only in wh-03
    conn.execute("INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved) VALUES ('inv-sp-1', 'wh-01', ?, 50, 0)", (p1,))
    conn.execute("INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved) VALUES ('inv-sp-2', 'wh-02', ?, 50, 0)", (p2,))
    conn.execute("INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved) VALUES ('inv-sp-3', 'wh-03', ?, 50, 0)", (p3,))

    order_payload = {
        "customer_id": "usr-test-customer-01",
        "shipping_address": "777 Interstate Logistics Blvd, Columbus, OH",
        "destination_city": "Columbus",
        "destination_lat": 39.9980,
        "destination_lon": -82.8919,
        "items": [
            {"product_id": p1, "quantity": 4},
            {"product_id": p2, "quantity": 5},
            {"product_id": p3, "quantity": 6},
        ]
    }

    order = create_order_atomic(**order_payload)
    assert order["status"] == "CONFIRMED"
    order_id = order["order_id"]

    # Verify order items allocated to distinct warehouses
    allocated_whs = {item["allocated_warehouse_id"] for item in order["items"]}
    assert allocated_whs == {"wh-01", "wh-02", "wh-03"}

    # Verify shipments: exactly 3 shipments created, one per origin warehouse
    cur = conn.cursor()
    cur.execute("SELECT id, origin_warehouse_id, tracking_number, status FROM shipments WHERE order_id = ?", (order_id,))
    shipments = cur.fetchall()
    assert len(shipments) == 3
    shipment_origins = {s["origin_warehouse_id"] for s in shipments}
    assert shipment_origins == {"wh-01", "wh-02", "wh-03"}
    for s in shipments:
        assert s["status"] == "CREATED"

    # Verify reservations held in all 3 warehouse ledgers
    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = ? AND warehouse_id = 'wh-01'", (p1,))
    assert cur.fetchone()["quantity_reserved"] == 4

    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = ? AND warehouse_id = 'wh-02'", (p2,))
    assert cur.fetchone()["quantity_reserved"] == 5

    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = ? AND warehouse_id = 'wh-03'", (p3,))
    assert cur.fetchone()["quantity_reserved"] == 6


def test_adversarial_multi_warehouse_atomicity_rollback_on_late_stockout(isolated_db):
    """
    Transactional integrity: Line 1 succeeds in wh-01, Line 2 succeeds in wh-02,
    but Line 3 fails (insufficient stock in wh-03).
    Verifies strict rollback: lines 1 and 2 reservations MUST be rolled back!
    """
    conn = get_db_connection(isolated_db)

    p1, p2, p3 = "prod-rb-01", "prod-rb-02", "prod-rb-03"
    for p_id, sku in [(p1, "SKU-RB1"), (p2, "SKU-RB2"), (p3, "SKU-RB3")]:
        conn.execute("""
            INSERT OR REPLACE INTO products (id, sku, name, category, unit_price, weight_kg, reorder_threshold)
            VALUES (?, ?, 'Rollback Product', 'Testing', 15.0, 1.0, 5)
        """, (p_id, sku))

    conn.execute("INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved) VALUES ('inv-rb-1', 'wh-01', ?, 20, 0)", (p1,))
    conn.execute("INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved) VALUES ('inv-rb-2', 'wh-02', ?, 20, 0)", (p2,))
    conn.execute("INSERT OR REPLACE INTO inventory (id, warehouse_id, product_id, quantity_on_hand, quantity_reserved) VALUES ('inv-rb-3', 'wh-03', ?, 5, 0)", (p3,))

    order_payload = {
        "customer_id": "usr-test-customer-01",
        "shipping_address": "123 Rollback St, Chicago, IL",
        "items": [
            {"product_id": p1, "quantity": 10},  # Can be satisfied by wh-01
            {"product_id": p2, "quantity": 10},  # Can be satisfied by wh-02
            {"product_id": p3, "quantity": 50},  # IMPOSSIBLE: wh-03 only has 5
        ]
    }

    with pytest.raises(InsufficientStockException):
        create_order_atomic(**order_payload)

    # Verify atomicity: wh-01 and wh-02 reservations MUST BE ROLLED BACK TO 0
    cur = conn.cursor()
    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = ? AND warehouse_id = 'wh-01'", (p1,))
    assert cur.fetchone()["quantity_reserved"] == 0

    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = ? AND warehouse_id = 'wh-02'", (p2,))
    assert cur.fetchone()["quantity_reserved"] == 0

    # Verify no orphan order or shipments exist
    cur.execute("SELECT COUNT(*) FROM orders WHERE shipping_address = '123 Rollback St, Chicago, IL'")
    assert cur.fetchone()[0] == 0


def test_adversarial_order_atomic_payment_failure_rollback(isolated_db):
    """Failure simulation: Verify simulated payment failure rolls back all reservations and records."""
    conn = get_db_connection(isolated_db)

    cur = conn.cursor()
    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = 'prod-01' AND warehouse_id = 'wh-01'")
    initial_res = cur.fetchone()["quantity_reserved"]

    order_payload = {
        "customer_id": "usr-test-customer-01",
        "shipping_address": "888 Payment Failure Ave, Detroit, MI",
        "items": [{"product_id": "prod-01", "quantity": 3}],
        "simulate_payment_failure": True
    }

    with pytest.raises(PaymentFailedException, match="Payment processing failed"):
        create_order_atomic(**order_payload)

    # Check reservation: must remain at initial_res
    cur.execute("SELECT quantity_reserved FROM inventory WHERE product_id = 'prod-01' AND warehouse_id = 'wh-01'")
    assert cur.fetchone()["quantity_reserved"] == initial_res

    # Check orders table: no orphan record
    cur.execute("SELECT COUNT(*) FROM orders WHERE shipping_address = '888 Payment Failure Ave, Detroit, MI'")
    assert cur.fetchone()[0] == 0


def test_adversarial_order_atomic_idempotency_replay(isolated_db):
    """Idempotency test: Replay duplicate request with same idempotency_key returns identical order."""
    key = "IDEMP-KEY-TEST-2026-ADV"
    payload = {
        "customer_id": "usr-test-customer-01",
        "shipping_address": "999 Idempotency Way, Detroit, MI",
        "items": [{"product_id": "prod-01", "quantity": 1}],
        "idempotency_key": key
    }

    order1 = create_order_atomic(**payload)
    assert order1["order_id"] is not None

    # Replay request
    order2 = create_order_atomic(**payload)
    assert order2["order_id"] == order1["order_id"]
    assert order2["order_number"] == order1["order_number"]


# =============================================================================
# 3. 7-State Shipment FSM Illegal Transitions
# =============================================================================

def test_adversarial_shipment_fsm_terminal_state_delivered_immutability(isolated_db):
    """
    FSM Terminal State: Once DELIVERED, shipment is completely immutable.
    Attempts to transition to any other status or add milestones MUST be rejected.
    """
    shipment = create_shipment(
        order_id="ord_001",
        origin_warehouse_id="wh-01",
        tracking_number="TRK-ADV-DELIVERED-01"
    )
    trk = shipment["tracking_number"]

    # Valid progression to terminal DELIVERED
    update_shipment_status(trk, "PICKED_UP")
    update_shipment_status(trk, "IN_TRANSIT")
    update_shipment_status(trk, "OUT_FOR_DELIVERY")
    final_res = update_shipment_status(trk, "DELIVERED")
    assert final_res["status"] == "DELIVERED"

    # Attack: Attempt transitions from DELIVERED to any other state
    illegal_targets = ["IN_TRANSIT", "EXCEPTION", "RETURNED", "OUT_FOR_DELIVERY", "CREATED", "PICKED_UP"]
    for target in illegal_targets:
        with pytest.raises(ShipmentStateTransitionException, match="Cannot transition delivered shipment"):
            update_shipment_status(trk, target)

    # Attack: Attempt to record milestones on DELIVERED shipment
    with pytest.raises(ShipmentStateTransitionException, match="Cannot record milestone"):
        record_shipment_milestone(trk, "HUB_ARRIVAL", location_name="Midway Hub")

    with pytest.raises(ShipmentStateTransitionException, match="Cannot record milestone"):
        record_shipment_milestone(trk, "DELIVERY_COMPLETED", location_name="Front Door")


def test_adversarial_shipment_fsm_terminal_state_returned_immutability(isolated_db):
    """
    FSM Terminal State: Once RETURNED, shipment is completely immutable.
    Attempts to transition to any other status or add milestones MUST be rejected.
    """
    shipment = create_shipment(
        order_id="ord_001",
        origin_warehouse_id="wh-01",
        tracking_number="TRK-ADV-RETURNED-01"
    )
    trk = shipment["tracking_number"]

    # Progression: CREATED -> PICKED_UP -> IN_TRANSIT -> OUT_FOR_DELIVERY -> RETURNED
    update_shipment_status(trk, "PICKED_UP")
    update_shipment_status(trk, "IN_TRANSIT")
    update_shipment_status(trk, "OUT_FOR_DELIVERY")
    update_shipment_status(trk, "RETURNED")

    # Attack: Transitions from RETURNED
    illegal_targets = ["CREATED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED", "EXCEPTION"]
    for target in illegal_targets:
        with pytest.raises(ShipmentStateTransitionException, match="Cannot transition returned shipment"):
            update_shipment_status(trk, target)

    # Attack: Record milestone on RETURNED
    with pytest.raises(ShipmentStateTransitionException, match="Cannot record milestone"):
        record_shipment_milestone(trk, "SORTATION_COMPLETED", location_name="Return Facility")


def test_adversarial_shipment_fsm_illegal_skips_and_invalid_statuses(isolated_db):
    """FSM Integrity: Verify skipping intermediate states or passing invalid statuses fails."""
    shipment = create_shipment(
        order_id="ord_001",
        origin_warehouse_id="wh-01",
        tracking_number="TRK-ADV-SKIPS-01"
    )
    trk = shipment["tracking_number"]

    # Direct leaps from CREATED
    with pytest.raises(ShipmentStateTransitionException, match="Illegal state transition"):
        update_shipment_status(trk, "DELIVERED")

    with pytest.raises(ShipmentStateTransitionException, match="Illegal state transition"):
        update_shipment_status(trk, "OUT_FOR_DELIVERY")

    with pytest.raises(ShipmentStateTransitionException, match="Illegal state transition"):
        update_shipment_status(trk, "RETURNED")

    # Move to PICKED_UP
    update_shipment_status(trk, "PICKED_UP")

    # Direct leap from PICKED_UP to DELIVERED or RETURNED
    with pytest.raises(ShipmentStateTransitionException, match="Illegal state transition"):
        update_shipment_status(trk, "DELIVERED")

    with pytest.raises(ShipmentStateTransitionException, match="Illegal state transition"):
        update_shipment_status(trk, "RETURNED")

    # Non-existent status strings
    with pytest.raises(InvalidShipmentStatusException, match="Invalid shipment status"):
        update_shipment_status(trk, "FLYING")

    with pytest.raises(InvalidShipmentStatusException, match="Invalid shipment status"):
        update_shipment_status(trk, "LOST_IN_TRANSIT")

    with pytest.raises(InvalidShipmentStatusException, match="Invalid shipment status"):
        update_shipment_status(trk, "")


# =============================================================================
# 4. Polymorphic Telemetry
# =============================================================================

def test_adversarial_telemetry_out_of_bounds_coordinates(isolated_db):
    """Attack: Submit telemetry with invalid GPS coordinates violating Earth boundaries."""
    shipment = create_shipment(order_id="ord_001", origin_warehouse_id="wh-01", tracking_number="TRK-ADV-TELEM-01")
    trk = shipment["tracking_number"]

    # Latitude > 90.0
    with pytest.raises(InvalidTelemetryException, match="Invalid latitude value"):
        ingest_telemetry(trk, {"latitude": 90.0001, "longitude": -87.6})

    # Latitude < -90.0
    with pytest.raises(InvalidTelemetryException, match="Invalid latitude value"):
        ingest_telemetry(trk, {"latitude": -95.0, "longitude": -87.6})

    # Longitude > 180.0
    with pytest.raises(InvalidTelemetryException, match="Invalid longitude value"):
        ingest_telemetry(trk, {"latitude": 41.8, "longitude": 180.0001})

    # Longitude < -180.0
    with pytest.raises(InvalidTelemetryException, match="Invalid longitude value"):
        ingest_telemetry(trk, {"latitude": 41.8, "longitude": -180.0001})

    # Nested geo dict out-of-bounds
    with pytest.raises(InvalidTelemetryException, match="Invalid latitude value"):
        ingest_telemetry(trk, {"geo": {"lat": 105.0, "lon": 0.0}})

    # Non-numeric coordinate string
    with pytest.raises(InvalidTelemetryException, match="Invalid latitude value"):
        ingest_telemetry(trk, {"latitude": "NORTH_POLE", "longitude": 0.0})


def test_adversarial_telemetry_malformed_and_non_dict_payloads(isolated_db):
    """Attack: Submit non-dict telemetry payloads and nested malformed geo structures."""
    shipment = create_shipment(order_id="ord_001", origin_warehouse_id="wh-01", tracking_number="TRK-ADV-TELEM-02")
    trk = shipment["tracking_number"]

    # Non-dict root payloads
    with pytest.raises(InvalidTelemetryException, match="Telemetry payload must be a JSON object"):
        ingest_telemetry(trk, "latitude: 41.8, longitude: -87.6")

    with pytest.raises(InvalidTelemetryException, match="Telemetry payload must be a JSON object"):
        ingest_telemetry(trk, [41.8, -87.6])

    with pytest.raises(InvalidTelemetryException, match="Telemetry payload must be a JSON object"):
        ingest_telemetry(trk, 42)

    with pytest.raises(InvalidTelemetryException, match="Telemetry payload must be a JSON object"):
        ingest_telemetry(trk, None)

    # Nested geo as non-dict
    with pytest.raises(InvalidTelemetryException, match="Invalid geo specification"):
        ingest_telemetry(trk, {"geo": "Chicago, IL"})

    with pytest.raises(InvalidTelemetryException, match="Invalid geo specification"):
        ingest_telemetry(trk, {"geo": [41.8, -87.6]})


def test_adversarial_telemetry_non_existent_shipment(isolated_db):
    """Attack: Submit telemetry for non-existent shipment tracking number."""
    with pytest.raises(ShipmentNotFoundException, match="not found"):
        ingest_telemetry("TRK-TOTALLY-NONEXISTENT", {"latitude": 41.8, "longitude": -87.6})


def test_adversarial_telemetry_terminal_shipment_position_immutability(isolated_db):
    """
    Integrity: Telemetry sent after shipment is DELIVERED should be recorded in NoSQL
    for audit history, BUT the relational shipments.current_lat/current_lon must remain frozen.
    """
    shipment = create_shipment(
        order_id="ord_001",
        origin_warehouse_id="wh-01",
        tracking_number="TRK-ADV-TERM-TELEM"
    )
    trk = shipment["tracking_number"]

    # Move to DELIVERED
    update_shipment_status(trk, "IN_TRANSIT", location={"latitude": 41.8, "longitude": -87.6})
    update_shipment_status(trk, "DELIVERED", location={"latitude": 41.9, "longitude": -87.7})

    # Read relational coordinates before extra telemetry
    conn = get_db_connection(isolated_db)
    cur = conn.cursor()
    cur.execute("SELECT current_lat, current_lon FROM shipments WHERE tracking_number = ?", (trk,))
    before = cur.fetchone()
    frozen_lat, frozen_lon = before["current_lat"], before["current_lon"]

    # Ingest telemetry with distant coordinates (e.g. Los Angeles)
    ingest_telemetry(trk, {"latitude": 34.0522, "longitude": -118.2437, "status": "ping_after_delivered"})

    # Relational coordinates MUST NOT have changed!
    cur.execute("SELECT current_lat, current_lon FROM shipments WHERE tracking_number = ?", (trk,))
    after = cur.fetchone()
    assert after["current_lat"] == frozen_lat
    assert after["current_lon"] == frozen_lon


def test_adversarial_telemetry_sparse_and_large_payloads(isolated_db):
    """Robustness: Ingest sparse payload (no GPS) and large payload (huge telemetry metadata)."""
    shipment = create_shipment(order_id="ord_001", origin_warehouse_id="wh-01", tracking_number="TRK-ADV-LARGE-01")
    trk = shipment["tracking_number"]

    # Sparse: only environmental readings
    sparse_doc = ingest_telemetry(trk, {
        "device_id": "SENSOR-SPARSE-01",
        "temperature_celsius": -22.4,
        "shock_impact_g": 0.05
    })
    assert sparse_doc["_id"] is not None

    # Ultra-large: 20,000 char diagnostic payload
    large_payload = {
        "device_id": "SENSOR-DIAG-01",
        "latitude": 42.1,
        "longitude": -83.5,
        "raw_canbus_dump": "A" * 20000,
        "battery_health": {"voltage": 12.6, "cycles": 1420}
    }
    large_doc = ingest_telemetry(trk, large_payload)
    assert large_doc["_id"] is not None

    # Query back from NoSQL
    history = get_shipment_telemetry(trk, limit=10)
    assert len(history) >= 2


# =============================================================================
# 5. Graph Routing under Dynamic Network Perturbation
# =============================================================================

def test_adversarial_routing_astar_admissibility_guard(isolated_db):
    """
    Admissibility Guard: A* Haversine distance heuristic is admissible ONLY for
    distance-based optimization. If caller requests cost or time optimization with
    use_astar=True, RoutingException must be raised.
    """
    # Distance criteria with A* succeeds
    res_dist = calculate_optimal_route("CDC-CHI", "LSF-NYC", criteria="distance", use_astar=True)
    assert res_dist["algorithm"] == "A_STAR"
    assert res_dist["total_distance_km"] > 0

    # Cost criteria with A* MUST raise RoutingException
    with pytest.raises(RoutingException, match="A\\* Haversine heuristic is admissible only for distance-based optimization"):
        calculate_optimal_route("CDC-CHI", "LSF-NYC", criteria="cost", use_astar=True)

    # Time criteria with A* MUST raise RoutingException
    with pytest.raises(RoutingException, match="A\\* Haversine heuristic is admissible only for distance-based optimization"):
        calculate_optimal_route("CDC-CHI", "LSF-NYC", criteria="time", use_astar=True)


def test_adversarial_routing_simultaneous_multi_corridor_outages(isolated_db):
    """
    Dynamic Perturbation:
    1. Single corridor outage triggers automated fallback reroute.
    2. Simultaneous multi-corridor outage disconnecting all paths raises NoPathFoundException.
    """
    # Pacific-West verification hubs: Seattle -> Denver
    # Normal routes:
    # Route A (via Portland): Seattle -> Portland (corr_sea_pdx) -> Denver (corr_pdx_den) = 1880 km
    # Route B (via Salt Lake): Seattle -> Salt Lake (corr_sea_slc) -> Denver (corr_slc_den) = 2200 km

    # 1. Simulate outage of primary corridor corr_sea_pdx
    reroute = simulate_edge_outage_and_reroute(
        origin_node="Hub-Seattle",
        destination_node="Hub-Denver",
        failed_edge_id="corr_sea_pdx"
    )
    assert reroute["reroute_successful"] is True
    assert "Hub-SaltLake" in reroute["path_nodes"]
    assert reroute["route_delta"]["additional_distance_km"] > 0

    # 2. Simulate simultaneous outage of BOTH outbound corridors from Seattle
    with pytest.raises(NoPathFoundException, match="No navigable path connects"):
        simulate_edge_outage_and_reroute(
            origin_node="Hub-Seattle",
            destination_node="Hub-Denver",
            failed_edges=["corr_sea_pdx", "corr_sea_slc"]
        )


def test_adversarial_routing_unreachable_node_and_nonexistent_node(isolated_db):
    """Boundary conditions: Unknown hubs, empty strings, and self-loop 0-cost invariants."""
    # Unknown origin / destination
    with pytest.raises(NodeNotFoundException, match="not found"):
        calculate_optimal_route("UNKNOWN_HUB_XYZ", "CDC-CHI")

    with pytest.raises(NodeNotFoundException, match="not found"):
        calculate_optimal_route("CDC-CHI", "ATLANTIS_PORT")

    with pytest.raises(NodeNotFoundException, match="cannot be empty"):
        calculate_optimal_route("", "CDC-CHI")

    # Self-loop boundary condition: origin == destination returns 0-cost path
    self_loop = calculate_optimal_route("CDC-CHI", "CDC-CHI")
    assert self_loop["hop_count"] == 0
    assert self_loop["total_distance_km"] == 0.0
    assert self_loop["total_cost_usd"] == 0.0
    assert self_loop["path_nodes"] == ["CDC-CHI"]

    # Seeded SQL CTE self-loop boundary condition
    cte_self = seeded_network_cte_routing("CDC-CHI", "CDC-CHI")
    assert cte_self["hops"] == 0
    assert cte_self["total_distance_km"] == 0.0


def test_adversarial_routing_vehicle_weight_constraint_pruning(isolated_db):
    """Constraint Pruning: Vehicle weight exceeding corridor capacity limit fails."""
    # Enormous vehicle (1,000,000 kg) exceeds all road freight limits (max ~40,000 kg)
    with pytest.raises(VehicleConstraintViolationException, match="exceeds all viable corridors"):
        calculate_optimal_route(
            origin_node="CDC-CHI",
            destination_node="RDC-DET",
            vehicle_constraints={"weight_kg": 1000000}
        )


# =============================================================================
# 6. Cache-Aside Invalidation Lifecycle
# =============================================================================

def test_adversarial_cache_manager_lru_eviction_and_capacity_bound():
    """Cache Internals: Verify strict LRU eviction order and capacity bounds."""
    # Small cache with capacity = 3
    cache = CacheManager(max_entries=3, default_ttl=300)

    cache.set("k1", "val1")
    cache.set("k2", "val2")
    cache.set("k3", "val3")
    assert cache.get_stats()["size"] == 3

    # Access k1, making k2 the least recently used
    assert cache.get("k1") == "val1"

    # Insert k4 -> should evict k2!
    cache.set("k4", "val4")
    stats = cache.get_stats()
    assert stats["size"] == 3
    assert stats["evictions"] == 1

    # Verify k2 was evicted, others remain
    assert cache.get("k2") is None
    assert cache.get("k1") == "val1"
    assert cache.get("k3") == "val3"
    assert cache.get("k4") == "val4"


def test_adversarial_cache_manager_ttl_expiry_and_pruning():
    """Cache Internals: Verify sub-second TTL expiration and immediate expiration on TTL <= 0."""
    cache = CacheManager(max_entries=10, default_ttl=300)

    # 1. Short TTL expiration
    cache.set("temp_key", "temporary_data", ttl_seconds=0.05)
    assert cache.get("temp_key") == "temporary_data"

    time.sleep(0.06)
    # Lazy expiry on get
    assert cache.get("temp_key") is None
    assert cache.get_stats()["invalidations"] >= 1

    # 2. Immediate expiration when ttl <= 0
    cache.set("instant_die", "doomed", ttl_seconds=0)
    assert cache.get("instant_die") is None


def test_adversarial_cache_manager_glob_pattern_invalidation():
    """Cache Invalidation: Verify glob pattern invalidation precision."""
    cache = CacheManager(max_entries=50, default_ttl=300)

    cache.set("shipment:track:TRK-01", "data-01")
    cache.set("shipment:track:TRK-02", "data-02")
    cache.set("shipment:milestones:TRK-01", "milestones-01")
    cache.set("inventory:wh-01:prod-01", "inv-01")
    cache.set("inventory:wh-02:prod-01", "inv-02")

    # Invalidate only shipment tracking keys
    deleted_count = cache.invalidate_pattern("shipment:track:*")
    assert deleted_count == 2
    assert cache.get("shipment:track:TRK-01") is None
    assert cache.get("shipment:track:TRK-02") is None

    # Other namespaces MUST remain untouched
    assert cache.get("shipment:milestones:TRK-01") == "milestones-01"
    assert cache.get("inventory:wh-01:prod-01") == "inv-01"
    assert cache.get("inventory:wh-02:prod-01") == "inv-02"


def test_adversarial_cache_stats_and_hit_ratio_calculation():
    """Telemetry: Verify telemetry counters and hit_ratio mathematical accuracy."""
    cache = CacheManager(max_entries=10, default_ttl=300)

    # Empty cache: hit_ratio is 0.0
    assert cache.get_stats()["hit_ratio"] == 0.0

    cache.set("k1", "v1")
    cache.get("k1")  # Hit 1
    cache.get("k1")  # Hit 2
    cache.get("missing_1")  # Miss 1
    cache.get("missing_2")  # Miss 2

    # 2 hits out of 4 requests = 0.50
    stats = cache.get_stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 2
    assert stats["hit_ratio"] == 0.5

    # Reset stats
    cache.reset_stats()
    reset_stats = cache.get_stats()
    assert reset_stats["hits"] == 0
    assert reset_stats["misses"] == 0
    assert reset_stats["hit_ratio"] == 0.0

    # Cache entries themselves remain intact after reset_stats()
    assert cache.get("k1") == "v1"


def test_adversarial_cache_aside_shipment_invalidation_coherence(isolated_db):
    """
    End-to-end Cache-Aside Coherence:
    Read -> HIT -> Mutation (update status) -> Invalidation -> MISS with updated data.
    """
    shipment = create_shipment(order_id="ord_001", origin_warehouse_id="wh-01", tracking_number="TRK-ADV-CACHE-COH")
    trk = shipment["tracking_number"]

    # 1. First read is MISS
    d1 = get_tracking_details(trk)
    assert d1["cache_status"] == "MISS"
    assert d1["status"] == "CREATED"

    # 2. Second read is HIT
    d2 = get_tracking_details(trk)
    assert d2["cache_status"] == "HIT"
    assert d2["status"] == "CREATED"

    # 3. Mutate shipment: update status to PICKED_UP
    update_shipment_status(trk, "PICKED_UP")

    # 4. Third read MUST be MISS and reflect new status PICKED_UP
    d3 = get_tracking_details(trk)
    assert d3["cache_status"] == "MISS"
    assert d3["status"] == "PICKED_UP"

    # 5. Fourth read is HIT with PICKED_UP
    d4 = get_tracking_details(trk)
    assert d4["cache_status"] == "HIT"
    assert d4["status"] == "PICKED_UP"


# =============================================================================
# 7. Transaction Atomicity & Storage/NoSQL Security
# =============================================================================

def test_adversarial_transaction_scope_clean_rollback_on_exception(isolated_db):
    """ACID Atomicity: Mid-operation unhandled exception triggers clean rollback of all mutations."""
    conn = get_db_connection(isolated_db)

    test_wh_id = "wh-atomic-crash-01"

    with pytest.raises(RuntimeError, match="Simulated application failure"):
        with transaction_scope(conn) as tx:
            tx.execute("""
                INSERT INTO warehouses (id, code, name, type, address, city, country, latitude, longitude, capacity_sqm)
                VALUES (?, 'WH-CRASH', 'Crash Test WH', 'HUB', '123 Crash Way', 'Crash City', 'USA', 40.0, -80.0, 10000)
            """, (test_wh_id,))
            raise RuntimeError("Simulated application failure mid-transaction!")

    # Verify that test warehouse was NEVER committed
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM warehouses WHERE id = ?", (test_wh_id,))
    assert cur.fetchone()[0] == 0


def test_adversarial_transaction_nested_savepoint_isolation(isolated_db):
    """
    ACID Savepoints: Nested transaction rollback aborts only inner mutations
    while outer transaction cleanly commits.
    """
    conn = get_db_connection(isolated_db)

    outer_id = "wh-savepoint-outer"
    inner_id = "wh-savepoint-inner"

    with transaction_scope(conn) as outer_tx:
        outer_tx.execute("""
            INSERT INTO warehouses (id, code, name, type, address, city, country, latitude, longitude, capacity_sqm)
            VALUES (?, 'WH-SP-OUTER', 'Outer WH', 'HUB', '100 Outer Rd', 'Outer City', 'USA', 41.0, -81.0, 10000)
        """, (outer_id,))

        # Nested savepoint that aborts
        try:
            with transaction_scope(outer_tx) as inner_tx:
                inner_tx.execute("""
                    INSERT INTO warehouses (id, code, name, type, address, city, country, latitude, longitude, capacity_sqm)
                    VALUES (?, 'WH-SP-INNER', 'Inner WH', 'HUB', '200 Inner Rd', 'Inner City', 'USA', 42.0, -82.0, 10000)
                """, (inner_id,))
                raise ValueError("Inner nested transaction aborted!")
        except ValueError:
            pass  # Handled: outer transaction continues

    # Verify: outer_id exists, inner_id does NOT exist
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM warehouses WHERE id = ?", (outer_id,))
    assert cur.fetchone()[0] == 1

    cur.execute("SELECT COUNT(*) FROM warehouses WHERE id = ?", (inner_id,))
    assert cur.fetchone()[0] == 0


def test_adversarial_transaction_direct_constraint_violation(isolated_db):
    """Database Integrity: Relational CHECK constraint violation triggers rollback."""
    conn = get_db_connection(isolated_db)

    # CHECK (quantity_on_hand >= quantity_reserved)
    with pytest.raises(sqlite3.IntegrityError):
        with transaction_scope(conn) as tx:
            tx.execute("""
                UPDATE inventory 
                SET quantity_reserved = quantity_on_hand + 500
                WHERE warehouse_id = 'wh-01' AND product_id = 'prod-01'
            """)


def test_adversarial_storage_directory_traversal_and_device_names():
    """Security Defense: LocalStorageClient defends against path traversal and Windows device names."""
    storage = LocalStorageClient()

    # Traversal attempts
    with pytest.raises(ValueError, match="Security error"):
        storage.upload_file(b"data", "../../etc/shadow")

    with pytest.raises(ValueError, match="Security error"):
        storage.upload_file(b"data", "/etc/passwd")

    with pytest.raises(ValueError, match="Security error"):
        storage.upload_file(b"data", "\\windows\\system32\\cmd.exe")

    with pytest.raises(ValueError, match="Security error"):
        storage.upload_file(b"data", "C:\\Windows\\System32\\cmd.exe")

    with pytest.raises(ValueError, match="Security error"):
        storage.upload_file(b"data", "\\\\network\\share\\exploit.exe")

    # Windows reserved device names
    for dev in ["NUL", "CON", "PRN", "AUX", "COM1", "LPT1"]:
        with pytest.raises(ValueError, match="Reserved Windows device name"):
            storage.upload_file(b"data", dev)

        with pytest.raises(ValueError, match="Reserved Windows device name"):
            storage.upload_file(b"data", f"{dev}.txt")

    # Empty filenames
    with pytest.raises(ValueError, match="Filename cannot be empty"):
        storage.upload_file(b"data", "")

    with pytest.raises(ValueError, match="Filename cannot be empty"):
        storage.upload_file(b"data", "    ")


def test_adversarial_nosql_injection_and_path_sanitization(isolated_db):
    """Security Defense: DocumentStore defends against operator and path injection attacks."""
    doc_store = get_doc_store(isolated_db)

    # 1. Invalid field path for expression index
    with pytest.raises(ValueError, match="Invalid field path for index"):
        doc_store.create_index("shipment_telemetry", "geo.lat; DROP TABLE nosql_documents;--")

    # 2. Invalid collection name for expression index
    with pytest.raises(ValueError, match="Invalid collection name"):
        doc_store.create_index("collection; DROP TABLE users;--", "geo.latitude")

    # 3. $in with non-iterable sequence
    with pytest.raises(ValueError, match="\\$in requires an iterable sequence"):
        doc_store.find("shipment_telemetry", {"geo.latitude": {"$in": 42.0}})

    # 4. $nin with non-iterable sequence
    with pytest.raises(ValueError, match="\\$nin requires an iterable sequence"):
        doc_store.find("shipment_telemetry", {"geo.latitude": {"$nin": "not-a-list"}})

    # 5. Unsupported query operator
    with pytest.raises(ValueError, match="Unsupported query operator"):
        doc_store.find("shipment_telemetry", {"geo.latitude": {"$regex": ".*"}})

    # 6. Invalid sort field injection
    with pytest.raises(ValueError, match="Invalid sort field name"):
        doc_store.find("shipment_telemetry", sort_by="timestamp; DROP TABLE nosql_documents;--")
