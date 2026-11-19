import sqlite3
import os
import json

db_path = os.path.abspath("data/logistics.db")
print("==================================================================")
print("  LOGISTICS IMS - DATABASE INSPECTION REPORT")
print("==================================================================")
print(f"File Location : {db_path}")
print(f"File Size     : {os.path.getsize(db_path):,} bytes ({os.path.getsize(db_path)/1024:.1f} KB)")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("PRAGMA journal_mode;")
print(f"Journal Mode  : {cur.fetchone()[0].upper()} (Write-Ahead Logging for high concurrency)")
cur.execute("PRAGMA foreign_keys;")
print(f"Foreign Keys  : {'ON (Enforced)' if cur.fetchone()[0] == 1 else 'OFF'}")

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = [row[0] for row in cur.fetchall()]
print(f"\nTotal Tables  : {len(tables)} tables")
print("------------------------------------------------------------------")
for t in tables:
    cur.execute(f"SELECT count(*) FROM [{t}];")
    cnt = cur.fetchone()[0]
    print(f"  • {t:<22} : {cnt:>4} rows")

print("\n1. SAMPLE: WAREHOUSES TABLE (`warehouses`)")
print("------------------------------------------------------------------")
cur.execute("SELECT code, name, city, state, capacity_sqm FROM warehouses LIMIT 5;")
for row in cur.fetchall():
    print(f"  [{row[0]}] {row[1]:<28} | {row[2]}, {row[3]:<2} | Capacity: {row[4]:,} sqm")

print("\n2. SAMPLE: PRODUCTS & INVENTORY BALANCE (`products` + `inventory`)")
print("------------------------------------------------------------------")
cur.execute("""
    SELECT p.sku, p.name, w.code, i.quantity_on_hand, i.quantity_reserved, p.reorder_threshold
    FROM products p 
    JOIN inventory i ON p.id = i.product_id 
    JOIN warehouses w ON i.warehouse_id = w.id
    LIMIT 5;
""")
for row in cur.fetchall():
    avail = row[3] - row[4]
    status = "⚠️ REORDER" if avail <= row[5] else "✅ HEALTHY"
    print(f"  [{row[0]}] {row[1][:22]:<22} @ {row[2]} | On-Hand: {row[3]:>3} | Rsvd: {row[4]:>2} | Avail: {avail:>3} | {status}")

print("\n3. SAMPLE: SHIPMENTS (7-STATE FSM) (`shipments`)")
print("------------------------------------------------------------------")
cur.execute("""
    SELECT s.tracking_number, s.status, s.carrier, s.service_level, w.code
    FROM shipments s
    JOIN warehouses w ON s.origin_warehouse_id = w.id
    LIMIT 6;
""")
for row in cur.fetchall():
    print(f"  {row[0]:<22} | Status: {row[1]:<16} | Carrier: {row[2]:<6} | Origin: {row[4]}")

print("\n4. SAMPLE: NoSQL DOCUMENT STORE (`nosql_documents` via SQLite JSON1)")
print("------------------------------------------------------------------")
cur.execute("SELECT collection, data FROM nosql_documents LIMIT 2;")
for row in cur.fetchall():
    parsed = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    formatted = json.dumps(parsed, indent=2)
    print(f"Collection: [{row[0]}]\nJSON Payload:\n{formatted}\n")

conn.close()
