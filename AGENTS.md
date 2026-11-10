# Project Rules & Invariants — Logistics Management System

## 1. SQLite Foreign Key UPSERT Rule

When writing SQL statements or test setups for SQLite databases where foreign keys are enabled (`PRAGMA foreign_keys = ON`):
- Avoid `INSERT OR REPLACE INTO parent_table` if child tables reference the parent primary key. In SQLite, `INSERT OR REPLACE` internally executes a `DELETE` followed by an `INSERT`, triggering cascade violations or `sqlite3.IntegrityError: FOREIGN KEY constraint failed` on referencing child rows.
- Instead, use explicit UPSERT syntax:
  ```sql
  INSERT INTO parent_table (id, col1, col2)
  VALUES (?, ?, ?)
  ON CONFLICT (id) DO UPDATE SET
      col1 = excluded.col1,
      col2 = excluded.col2;
  ```
- Or explicitly delete referencing child rows first before executing replacement logic.

## 2. Multi-Warehouse Stock Invariant

All warehouse inventory operations must preserve the non-negative availability invariant:
$$Q_{\text{available}} = Q_{\text{on\_hand}} - Q_{\text{reserved}} \ge 0$$
- Allocations must reserve stock before order confirmation.
- Cancellations or failed fulfillments must release reserved stock atomically without leaving orphan holds.

## 3. Shipment Finite State Machine Transitions

Shipment status progressions must follow valid FSM paths:
- `CREATED` -> `PICKED_UP` or `IN_TRANSIT` (express departure)
- `PICKED_UP` -> `IN_TRANSIT`
- `IN_TRANSIT` -> `OUT_FOR_DELIVERY` or `DELIVERED` (express direct delivery)
- Terminal states (`DELIVERED`, `RETURNED`) are immutable and strictly reject further status mutations.
