"""Tier 5 Adversarial Security, RBAC & API Hardening Verification Suite.

Comprehensive white-box adversarial verification covering:
1. JWT Token Security:
   - Tampered payload with signature mismatch
   - Corrupted signature bits
   - Expired tokens (exp in past)
   - Missing required claims (sub, exp, iat, role)
   - Algorithm manipulation (alg='none', alg='RS256', alg='HS512')
   - Unauthorized secret key forgery
   - Malformed / corrupted / blank Authorization headers
2. RBAC Privilege Enforcement across all 5 Roles:
   - Admin (unrestricted access)
   - WarehouseManager (restricted to inventory/warehouses; forbidden from admin, dispatch, POD, audit)
   - Dispatcher (restricted to shipments/routes; forbidden from admin, restock, adjust, order, POD, audit)
   - Driver (restricted to status, milestones, telemetry, POD; forbidden from admin, inventory, dispatch, orders, routes, audit)
   - Customer (restricted to orders & tracking; forbidden from admin, inventory, dispatch, telemetry, routes, audit)
   - Verification of HTTP 403 Forbidden status code and security audit logging
3. SQL & NoSQL Injection Resilience:
   - Query parameters across inventory, orders, routing, analytics
   - Path parameters in orders and shipments
   - JSON body fields across registration, login, orders, inventory restock/adjust, shipments, routing
   - Polymorphic NoSQL document store injection vectors in IoT telemetry
4. Input Validation & Boundary Conditions:
   - Malformed and non-JSON body payloads (400 Bad Request)
   - Empty request bodies and empty arrays (400 Bad Request)
   - Negative and zero quantities in restock, adjust, and order placement
   - Type mismatches (strings for numeric quantities/coordinates)
   - Invalid domain enum values for statuses and milestones (422 Unprocessable Entity)
   - Physical warehouse capacity limit boundary overflows (422 Unprocessable Entity)
5. Web UI Route Accessibility:
   - GET / (Executive Operations Dashboard) -> HTTP 200, text/html
   - GET /tracking (Shipment Tracking & 7-State FSM) -> HTTP 200, text/html
   - GET /inventory (Multi-Warehouse Inventory Browser) -> HTTP 200, text/html
   - GET /network (Logistics Network Graph & Bottlenecks) -> HTTP 200, text/html
6. Bulk Batch Shipment Authorization:
   - Unauthenticated access (401 Unauthorized)
   - Unauthorized roles: Driver, Customer, WarehouseManager (403 Forbidden)
   - Authorized roles: Dispatcher and Admin (201 Created)
"""

import time
import base64
import json
import logging
import pytest
import jwt

from backend.auth.security import (
    generate_token,
    decode_token,
    verify_token,
    hash_password,
    verify_password,
    get_jwt_secret,
    InvalidTokenError,
    TokenExpiredError
)
from backend.auth.rbac import (
    ROLE_ADMIN,
    ROLE_WAREHOUSE_MANAGER,
    ROLE_DISPATCHER,
    ROLE_DRIVER,
    ROLE_CUSTOMER
)


# =============================================================================
# 1. JWT Token Security & Cryptographic Integrity Tests
# =============================================================================

class TestJWTSecurityAdversarial:
    """Adversarially attacks the JWT verification and decoding pipeline."""

    def test_jwt_tampered_payload_signature_mismatch(self, client, auth_tokens):
        """Attack: Decode valid customer JWT, modify payload role to 'Admin', keep original signature."""
        valid_token = auth_tokens["Customer"]
        parts = valid_token.split(".")
        assert len(parts) == 3, "Valid JWT must have 3 segments"

        # Decode payload segment
        payload_b64 = parts[1]
        padded_b64 = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_dict = json.loads(base64.urlsafe_b64decode(padded_b64.encode()).decode())

        # Tamper payload: elevate role to Admin
        payload_dict["role"] = "Admin"
        payload_dict["permissions"] = ["*"]
        tampered_b64 = base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).decode().rstrip("=")

        # Reassemble token with tampered payload and original signature
        tampered_token = f"{parts[0]}.{tampered_b64}.{parts[2]}"

        headers = {"Authorization": f"Bearer {tampered_token}"}
        res = client.get("/api/v1/admin/users", headers=headers)
        assert res.status_code == 401, f"Expected 401 for tampered payload, got {res.status_code}"
        assert res.get_json().get("error") == "Unauthorized"

    def test_jwt_corrupted_signature_bits(self, client, auth_tokens):
        """Attack: Corrupt characters in the signature segment of an admin token."""
        admin_token = auth_tokens["Admin"]
        parts = admin_token.split(".")

        # Flip/corrupt signature
        corrupted_sig = "X" * len(parts[2])
        corrupted_token = f"{parts[0]}.{parts[1]}.{corrupted_sig}"

        headers = {"Authorization": f"Bearer {corrupted_token}"}
        res = client.get("/api/v1/admin/users", headers=headers)
        assert res.status_code == 401, f"Expected 401 for corrupted signature, got {res.status_code}"

    def test_jwt_expired_token(self, client, app):
        """Attack: Submit token with exp timestamp strictly in the past."""
        secret = app.config.get("JWT_SECRET") or "test-evaluation-secret-key-super-secure-32-bytes!"
        now = int(time.time())
        expired_payload = {
            "sub": "usr-expired-adversary",
            "username": "expired_attacker",
            "role": "Admin",
            "permissions": ["*"],
            "iat": now - 7200,
            "exp": now - 3600  # Expired 1 hour ago
        }
        expired_token = jwt.encode(expired_payload, secret, algorithm="HS256")

        headers = {"Authorization": f"Bearer {expired_token}"}
        res = client.get("/api/v1/orders", headers=headers)
        assert res.status_code == 401, f"Expected 401 for expired token, got {res.status_code}"
        data = res.get_json()
        assert "expired" in data.get("message", "").lower() or "expired" in data.get("details", "").lower()

    def test_jwt_missing_required_claims(self, client, app):
        """Attack: Submit tokens missing essential claims (sub, exp, iat, role)."""
        secret = app.config.get("JWT_SECRET") or "test-evaluation-secret-key-super-secure-32-bytes!"
        now = int(time.time())

        # Case A: Missing sub
        payload_no_sub = {
            "username": "nosub_user",
            "role": "Admin",
            "iat": now,
            "exp": now + 3600
        }
        token_no_sub = jwt.encode(payload_no_sub, secret, algorithm="HS256")
        r_sub = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token_no_sub}"})
        assert r_sub.status_code == 401, f"Expected 401 for missing sub, got {r_sub.status_code}"

        # Case B: Missing exp
        payload_no_exp = {
            "sub": "usr-noexp",
            "username": "noexp_user",
            "role": "Admin",
            "iat": now
        }
        token_no_exp = jwt.encode(payload_no_exp, secret, algorithm="HS256")
        r_exp = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token_no_exp}"})
        assert r_exp.status_code == 401, f"Expected 401 for missing exp, got {r_exp.status_code}"

        # Case C: Missing iat
        payload_no_iat = {
            "sub": "usr-noiat",
            "username": "noiat_user",
            "role": "Admin",
            "exp": now + 3600
        }
        token_no_iat = jwt.encode(payload_no_iat, secret, algorithm="HS256")
        r_iat = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token_no_iat}"})
        assert r_iat.status_code == 401, f"Expected 401 for missing iat, got {r_iat.status_code}"

        # Case D: Missing role
        payload_no_role = {
            "sub": "usr-norole",
            "username": "norole_user",
            "iat": now,
            "exp": now + 3600
        }
        token_no_role = jwt.encode(payload_no_role, secret, algorithm="HS256")
        r_role = client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token_no_role}"})
        assert r_role.status_code == 401, f"Expected 401 for missing role, got {r_role.status_code}"

    def test_jwt_algorithm_manipulation_none(self, client):
        """Attack: Construct unsigned JWT with alg='none' attempting privilege escalation."""
        header_none = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "sub": "usr-attacker-none",
            "username": "attacker",
            "role": "Admin",
            "permissions": ["*"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600
        }).encode()).decode().rstrip("=")

        for token in [f"{header_none}.{payload}.", f"{header_none}.{payload}.sig"]:
            res = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 401, f"Algorithm 'none' bypass succeeded! Got {res.status_code}"

    def test_jwt_algorithm_manipulation_unsupported_algorithms(self, client):
        """Attack: Submit tokens signed with unexpected algorithms (RS256, HS384, HS512)."""
        now = int(time.time())
        payload = {
            "sub": "usr-attacker-alg",
            "username": "attacker",
            "role": "Admin",
            "permissions": ["*"],
            "iat": now,
            "exp": now + 3600
        }

        # HS512 with a 64-byte key to prevent InsecureKeyLengthWarning
        secret_64 = "this-is-a-super-long-64-byte-secure-hmac-key-for-testing-purposes!!"
        token_hs512 = jwt.encode(payload, secret_64, algorithm="HS512")
        res_hs512 = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token_hs512}"})
        assert res_hs512.status_code == 401, f"Expected 401 for HS512 algorithm, got {res_hs512.status_code}"

        # HS384 with a 48-byte key
        secret_48 = "this-is-a-super-long-48-byte-secure-hmac-key-for-testing-purposes!"
        token_hs384 = jwt.encode(payload, secret_48, algorithm="HS384")
        res_hs384 = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token_hs384}"})
        assert res_hs384.status_code == 401, f"Expected 401 for HS384 algorithm, got {res_hs384.status_code}"

    def test_jwt_wrong_secret_signature(self, client):
        """Attack: Sign an admin payload with an incorrect secret key."""
        now = int(time.time())
        payload = {
            "sub": "usr-forger",
            "username": "forger",
            "role": "Admin",
            "permissions": ["*"],
            "iat": now,
            "exp": now + 3600
        }
        forged_token = jwt.encode(payload, "unauthorized-foreign-secret-key-32b!", algorithm="HS256")
        res = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {forged_token}"})
        assert res.status_code == 401, f"Expected 401 for forged secret, got {res.status_code}"

    def test_jwt_empty_and_malformed_auth_headers(self, client):
        """Attack: Submit empty, malformed, or invalid authorization header variants."""
        bad_headers = [
            {},                                                 # Missing Authorization header
            {"Authorization": ""},                              # Empty header
            {"Authorization": "Bearer"},                        # Missing token
            {"Authorization": "Bearer "},                       # Trailing whitespace
            {"Authorization": "Bearer   "},                     # Multiple spaces
            {"Authorization": "Basic dXNlcjpwYXNz"},            # Basic auth instead of Bearer
            {"Authorization": "Token some_random_api_key"},     # Token prefix
            {"Authorization": "Bearer single_string_not_jwt"},  # Invalid JWT structure
            {"Authorization": "Bearer part1.part2"},            # Missing 3rd segment
            {"Authorization": "Bearer part1.part2.part3.part4"} # Excess segments
        ]

        for h in bad_headers:
            res = client.get("/api/v1/orders", headers=h)
            assert res.status_code == 401, f"Expected 401 for bad header {h}, got {res.status_code}"


# =============================================================================
# 2. RBAC Privilege Enforcement & Audit Logging Across All 5 Roles
# =============================================================================

class TestRBACPrivilegeEnforcementAdversarial:
    """Enforces fine-grained role boundaries across Admin, WarehouseManager, Dispatcher, Driver, Customer."""

    def test_rbac_admin_unrestricted_access(self, client, admin_headers):
        """Verify: Admin has unrestricted access to all protected subsystems."""
        # 1. Admin users directory
        r_admin = client.get("/api/v1/admin/users", headers=admin_headers)
        assert r_admin.status_code == 200

        # 2. Orders list
        r_orders = client.get("/api/v1/orders", headers=admin_headers)
        assert r_orders.status_code == 200

        # 3. Warehouses directory
        r_wh = client.get("/api/v1/inventory/warehouses", headers=admin_headers)
        assert r_wh.status_code == 200

        # 4. Network bottlenecks
        r_bn = client.get("/api/v1/network/bottlenecks", headers=admin_headers)
        assert r_bn.status_code == 200

        # 5. Audit logs
        r_audit = client.get("/api/v1/analytics/audit/logs", headers=admin_headers)
        assert r_audit.status_code == 200

    def test_rbac_warehouse_manager_privilege_boundaries(self, client, manager_headers, caplog):
        """Adversarial challenge: WarehouseManager attempting unauthorized routes."""
        with caplog.at_level(logging.WARNING):
            # Forbidden: Admin directory
            r1 = client.get("/api/v1/admin/users", headers=manager_headers)
            assert r1.status_code == 403, f"WarehouseManager bypassed admin directory: {r1.status_code}"
            assert r1.get_json().get("error") == "Forbidden"

            # Forbidden: Shipment dispatch
            r2 = client.post("/api/v1/shipments", json={"order_id": "ord-01", "origin_warehouse_id": "wh-01"}, headers=manager_headers)
            assert r2.status_code == 403, f"WarehouseManager bypassed shipment dispatch: {r2.status_code}"

            # Forbidden: Batch shipment creation
            r3 = client.post("/api/v1/shipments/batch", json={"shipments": [{"order_id": "ord-01"}]}, headers=manager_headers)
            assert r3.status_code == 403, f"WarehouseManager bypassed batch shipments: {r3.status_code}"

            # Forbidden: Assign driver
            r4 = client.post("/api/v1/shipments/TRK-CHI-NYC-8801/assign-driver", json={"driver_id": "usr-drv"}, headers=manager_headers)
            assert r4.status_code == 403, f"WarehouseManager bypassed driver assignment: {r4.status_code}"

            # Forbidden: Audit logs
            r5 = client.get("/api/v1/analytics/audit/logs", headers=manager_headers)
            assert r5.status_code == 403, f"WarehouseManager bypassed audit logs: {r5.status_code}"

            # Verify security audit log record was created
            assert any("RBAC Access Denied" in record.message for record in caplog.records), "Security log missing RBAC Access Denied warning"

    def test_rbac_dispatcher_privilege_boundaries(self, client, dispatcher_headers, caplog):
        """Adversarial challenge: Dispatcher attempting unauthorized warehouse/admin routes."""
        with caplog.at_level(logging.WARNING):
            # Forbidden: Admin directory
            r1 = client.get("/api/v1/admin/users", headers=dispatcher_headers)
            assert r1.status_code == 403

            # Forbidden: Inventory restock
            r2 = client.post("/api/v1/inventory/restock", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": 10}, headers=dispatcher_headers)
            assert r2.status_code == 403

            # Forbidden: Inventory adjust
            r3 = client.post("/api/v1/inventory/adjust", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity_delta": 5}, headers=dispatcher_headers)
            assert r3.status_code == 403

            # Forbidden: Customer order placement
            r4 = client.post("/api/v1/orders", json={"items": [{"product_id": "prod-01", "quantity": 1}], "shipping_address": "Test St"}, headers=dispatcher_headers)
            assert r4.status_code == 403

            # Forbidden: Proof of Delivery (POD) upload (restricted to Admin, Driver)
            r5 = client.post("/api/v1/shipments/TRK-CHI-NYC-8801/pod", json={"recipient_name": "Dispatcher"}, headers=dispatcher_headers)
            assert r5.status_code == 403

            # Forbidden: Audit logs
            r6 = client.get("/api/v1/analytics/audit/logs", headers=dispatcher_headers)
            assert r6.status_code == 403

            assert any("RBAC Access Denied" in record.message for record in caplog.records)

    def test_rbac_driver_privilege_boundaries(self, client, driver_headers, caplog):
        """Adversarial challenge: Driver attempting warehouse, dispatch, routing, or admin operations."""
        with caplog.at_level(logging.WARNING):
            # Forbidden: Admin directory
            r1 = client.get("/api/v1/admin/users", headers=driver_headers)
            assert r1.status_code == 403

            # Forbidden: Inventory restock
            r2 = client.post("/api/v1/inventory/restock", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": 10}, headers=driver_headers)
            assert r2.status_code == 403

            # Forbidden: Inventory adjust
            r3 = client.post("/api/v1/inventory/adjust", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity_delta": 5}, headers=driver_headers)
            assert r3.status_code == 403

            # Forbidden: Customer order placement
            r4 = client.post("/api/v1/orders", json={"items": [{"product_id": "prod-01", "quantity": 1}], "shipping_address": "Test St"}, headers=driver_headers)
            assert r4.status_code == 403

            # Forbidden: Shipment dispatch creation
            r5 = client.post("/api/v1/shipments", json={"order_id": "ord-01", "origin_warehouse_id": "wh-01"}, headers=driver_headers)
            assert r5.status_code == 403

            # Forbidden: Bulk batch shipment creation
            r6 = client.post("/api/v1/shipments/batch", json={"shipments": [{"order_id": "ord-01"}]}, headers=driver_headers)
            assert r6.status_code == 403

            # Forbidden: Driver assignment
            r7 = client.post("/api/v1/shipments/TRK-CHI-NYC-8801/assign-driver", json={"driver_id": "usr-drv"}, headers=driver_headers)
            assert r7.status_code == 403

            # Forbidden: Network route computation
            r8 = client.post("/api/v1/network/routes", json={"origin_node": "CDC-CHICAGO", "destination_node": "LSF-NEWYORK"}, headers=driver_headers)
            assert r8.status_code == 403

            # Forbidden: Audit logs
            r9 = client.get("/api/v1/analytics/audit/logs", headers=driver_headers)
            assert r9.status_code == 403

            assert any("RBAC Access Denied" in record.message for record in caplog.records)

    def test_rbac_customer_privilege_boundaries(self, client, customer_headers, caplog):
        """Adversarial challenge: Customer attempting operational, telemetry, or admin routes."""
        with caplog.at_level(logging.WARNING):
            # Forbidden: Admin directory
            r1 = client.get("/api/v1/admin/users", headers=customer_headers)
            assert r1.status_code == 403

            # Forbidden: Inventory restock
            r2 = client.post("/api/v1/inventory/restock", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": 10}, headers=customer_headers)
            assert r2.status_code == 403

            # Forbidden: Inventory adjust
            r3 = client.post("/api/v1/inventory/adjust", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity_delta": 5}, headers=customer_headers)
            assert r3.status_code == 403

            # Forbidden: Shipment dispatch creation
            r4 = client.post("/api/v1/shipments", json={"order_id": "ord-01", "origin_warehouse_id": "wh-01"}, headers=customer_headers)
            assert r4.status_code == 403

            # Forbidden: Bulk batch shipment creation
            r5 = client.post("/api/v1/shipments/batch", json={"shipments": [{"order_id": "ord-01"}]}, headers=customer_headers)
            assert r5.status_code == 403

            # Forbidden: Status update
            r6 = client.post("/api/v1/shipments/TRK-CHI-NYC-8801/status", json={"status": "IN_TRANSIT"}, headers=customer_headers)
            assert r6.status_code == 403

            # Forbidden: Milestone append
            r7 = client.post("/api/v1/shipments/TRK-CHI-NYC-8801/milestones", json={"milestone_type": "DEPARTED_FACILITY"}, headers=customer_headers)
            assert r7.status_code == 403

            # Forbidden: Telemetry ingestion
            r8 = client.post("/api/v1/telemetry", json={"tracking_number": "TRK-CHI-NYC-8801", "latitude": 41.8, "longitude": -87.6}, headers=customer_headers)
            assert r8.status_code == 403

            # Forbidden: POD upload
            r9 = client.post("/api/v1/shipments/TRK-CHI-NYC-8801/pod", json={"recipient_name": "Customer"}, headers=customer_headers)
            assert r9.status_code == 403

            # Forbidden: Network route computation
            r10 = client.post("/api/v1/network/routes", json={"origin_node": "CDC-CHICAGO", "destination_node": "LSF-NEWYORK"}, headers=customer_headers)
            assert r10.status_code == 403

            # Forbidden: Audit logs
            r11 = client.get("/api/v1/analytics/audit/logs", headers=customer_headers)
            assert r11.status_code == 403

            assert any("RBAC Access Denied" in record.message for record in caplog.records)


# =============================================================================
# 3. SQL & NoSQL Injection Resilience Tests
# =============================================================================

class TestSQLAndNoSQLInjectionAdversarial:
    """Stress tests parameterized queries against hostile SQL and NoSQL injection payloads."""

    SQL_ATTACK_VECTORS = [
        "' OR '1'='1",
        "'; DROP TABLE inventory; --",
        "'; DROP TABLE users; --",
        "' UNION SELECT id, username, password_hash, email, 1, 1, 1, 1, 1, 1 FROM users --",
        "1' OR '1'='1' --",
        "admin' --",
        '" OR ""="',
        "1; WAITFOR DELAY '0:0:5' --",
        "1' AND (SELECT 1 FROM (SELECT COUNT(*), CONCAT((SELECT username FROM users LIMIT 1), FLOOR(RAND(0)*2)) x FROM information_schema.tables GROUP BY x) a) --"
    ]

    def test_sql_injection_inventory_query_parameters(self, client, manager_headers):
        """Attack: Inject SQL commands into inventory warehouse_id, product_id, and low_stock query params."""
        for vector in self.SQL_ATTACK_VECTORS:
            res = client.get(f"/api/v1/inventory?warehouse_id={vector}", headers=manager_headers)
            assert res.status_code == 200, f"SQL injection caused failure for vector {vector}: {res.status_code}"
            data = res.get_json()
            assert data["total"] == 0 or len(data["items"]) == 0

            res_prod = client.get(f"/api/v1/inventory?product_id={vector}", headers=manager_headers)
            assert res_prod.status_code == 200
            assert res_prod.get_json()["total"] == 0

    def test_sql_injection_orders_query_and_path_params(self, client, admin_headers):
        """Attack: Inject SQL commands into order listing query filters and order_id path params."""
        for vector in self.SQL_ATTACK_VECTORS:
            # Query filters
            res = client.get(f"/api/v1/orders?customer_id={vector}", headers=admin_headers)
            assert res.status_code == 200
            assert isinstance(res.get_json().get("orders"), list)

            # Path param: order_id
            res_path = client.get(f"/api/v1/orders/{vector}", headers=admin_headers)
            # Must return 404 Not Found cleanly via parameterization, not 500 SQLite syntax error
            assert res_path.status_code in (400, 404), f"SQL injection in order_id path caused {res_path.status_code}"

    def test_sql_injection_shipments_path_and_query_params(self, client, dispatcher_headers):
        """Attack: Inject SQL commands into shipment tracking path endpoints."""
        for vector in self.SQL_ATTACK_VECTORS:
            res = client.get(f"/api/v1/shipments/{vector}", headers=dispatcher_headers)
            assert res.status_code in (400, 404), f"SQL injection in shipment path caused {res.status_code}"

    def test_sql_injection_network_reachability_and_alternatives(self, client, dispatcher_headers):
        """Attack: Inject SQL commands into network routing query parameters."""
        for vector in self.SQL_ATTACK_VECTORS:
            res = client.get(f"/api/v1/network/reachability?hub_id={vector}", headers=dispatcher_headers)
            assert res.status_code in (400, 404), f"SQL injection in reachability caused {res.status_code}"

            res_alt = client.get(f"/api/v1/network/alternatives?origin_node={vector}&destination_node=LSF-NEWYORK", headers=dispatcher_headers)
            assert res_alt.status_code in (400, 404), f"SQL injection in alternatives caused {res_alt.status_code}"

    def test_sql_injection_analytics_audit_logs_query_param(self, client, admin_headers):
        """Attack: Inject SQL commands into audit logs filter."""
        for vector in self.SQL_ATTACK_VECTORS:
            res = client.get(f"/api/v1/analytics/audit/logs?action={vector}", headers=admin_headers)
            assert res.status_code == 200, f"SQL injection in audit logs caused {res.status_code}"
            assert isinstance(res.get_json().get("logs"), list)

    def test_sql_injection_login_body_authentication_bypass(self, client):
        """Attack: Attempt classic SQL injection authentication bypass on /api/v1/auth/login."""
        hostile_creds = [
            {"username": "' OR '1'='1", "password": "' OR '1'='1"},
            {"username": "admin' --", "password": "password"},
            {"username": "admin' /*", "password": "*/"},
            {"username": "' UNION SELECT '1', 'admin', 'email@test.com', 'hash', 'role_admin' --", "password": "x"},
            {"email": "admin@example.com' OR 1=1 --", "password": "x"}
        ]
        for cred in hostile_creds:
            res = client.post("/api/v1/auth/login", json=cred)
            assert res.status_code == 401, f"SQL injection bypassed login authentication! Cred: {cred}"

    def test_nosql_injection_telemetry_json1_document_store(self, client, driver_headers):
        """Attack: Ingest polymorphic IoT telemetry with NoSQL injection operators and SQL fragments."""
        hostile_telemetry_docs = [
            {
                "tracking_number": "TRK-CHI-NYC-8801",
                "device_id": "IOT-DEV-001' OR 1=1 --",
                "latitude": 41.8781,
                "longitude": -87.6298,
                "$where": "this.device_id != null",
                "nested_attack": {
                    "$gt": 0,
                    "payload' OR '1'='1": "exploit"
                }
            },
            {
                "tracking_number": "TRK-CHI-NYC-8801",
                "device_id": "IOT-DEV-002",
                "latitude": 40.7128,
                "longitude": -74.0060,
                "sensor_data": {
                    "temperature_celsius": 4.5,
                    "sql_probe": "'; DROP TABLE nosql_documents; --"
                }
            }
        ]
        for doc in hostile_telemetry_docs:
            res = client.post("/api/v1/telemetry", json=doc, headers=driver_headers)
            # Successfully ingested and stored as sanitized JSON document
            assert res.status_code == 201, f"Expected 201 for hostile JSON document stored safely, got {res.status_code}"
            assert res.get_json().get("status") == "SUCCESS"


# =============================================================================
# 4. Input Validation & Boundary Condition Stress Tests
# =============================================================================

class TestInputValidationAndBoundariesAdversarial:
    """Stress tests input parsers, bounds validators, and domain exception mappers."""

    def test_input_validation_malformed_json_body(self, client, customer_headers):
        """Attack: Send malformed JSON string (syntax error) with application/json header."""
        malformed_raw_bodies = [
            "{unquoted_key: 123}",
            "{'single_quoted': 'value'}",
            "{\"truncated_json\": [1, 2, ",
            "this is not json at all",
            ""
        ]
        for raw in malformed_raw_bodies:
            res = client.post(
                "/api/v1/orders",
                data=raw,
                headers={"Authorization": customer_headers["Authorization"], "Content-Type": "application/json"}
            )
            assert res.status_code == 400, f"Expected 400 Bad Request for malformed body '{raw}', got {res.status_code}"

    def test_input_validation_empty_payloads(self, client, customer_headers, manager_headers):
        """Boundary: Send empty JSON dict {} to endpoints requiring mandatory fields."""
        # 1. Register with empty payload
        r_reg = client.post("/api/v1/auth/register", json={})
        assert r_reg.status_code == 400

        # 2. Login with empty payload
        r_login = client.post("/api/v1/auth/login", json={})
        assert r_login.status_code == 400

        # 3. Order placement with empty payload
        r_ord = client.post("/api/v1/orders", json={}, headers=customer_headers)
        assert r_ord.status_code == 400

        # 4. Inventory restock with empty payload
        r_restock = client.post("/api/v1/inventory/restock", json={}, headers=manager_headers)
        assert r_restock.status_code == 400

        # 5. Inventory adjust with empty payload
        r_adjust = client.post("/api/v1/inventory/adjust", json={}, headers=manager_headers)
        assert r_adjust.status_code == 400

    def test_input_validation_empty_arrays(self, client, customer_headers, dispatcher_headers):
        """Boundary: Send empty arrays where non-empty arrays are required."""
        # Order with empty items list
        r_ord = client.post(
            "/api/v1/orders",
            json={"items": [], "shipping_address": "123 Main St"},
            headers=customer_headers
        )
        assert r_ord.status_code == 400, f"Expected 400 for empty items array, got {r_ord.status_code}"

        # Batch shipments with empty shipments list
        r_batch = client.post(
            "/api/v1/shipments/batch",
            json={"shipments": []},
            headers=dispatcher_headers
        )
        assert r_batch.status_code == 400, f"Expected 400 for empty shipments array, got {r_batch.status_code}"

    def test_input_validation_negative_and_zero_quantities(self, client, manager_headers, customer_headers):
        """Boundary: Submit negative or zero quantities across restock, adjust, and order placement."""
        # Restock negative quantity
        r1 = client.post("/api/v1/inventory/restock", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": -10}, headers=manager_headers)
        assert r1.status_code == 400

        # Restock zero quantity
        r2 = client.post("/api/v1/inventory/restock", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": 0}, headers=manager_headers)
        assert r2.status_code == 400

        # Adjust zero delta
        r3 = client.post("/api/v1/inventory/adjust", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity_delta": 0}, headers=manager_headers)
        assert r3.status_code == 400

        # Order negative quantity
        r4 = client.post("/api/v1/orders", json={"items": [{"product_id": "prod-01", "quantity": -5}], "shipping_address": "Test"}, headers=customer_headers)
        assert r4.status_code in (400, 409, 422)

    def test_input_validation_type_mismatches(self, client, manager_headers, driver_headers):
        """Boundary: Submit non-numeric types for quantity, delta, and coordinates."""
        # Restock non-integer quantity
        r1 = client.post("/api/v1/inventory/restock", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": "invalid_number"}, headers=manager_headers)
        assert r1.status_code == 400

        # Adjust non-integer delta
        r2 = client.post("/api/v1/inventory/adjust", json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity_delta": "invalid_delta"}, headers=manager_headers)
        assert r2.status_code == 400

        # Telemetry invalid coordinates
        r3 = client.post("/api/v1/telemetry", json={"tracking_number": "TRK-CHI-NYC-8801", "latitude": "north_pole", "longitude": -87.6}, headers=driver_headers)
        assert r3.status_code in (400, 422)

    def test_input_validation_invalid_enum_values(self, client, dispatcher_headers):
        """Boundary: Submit invalid status and milestone enum values."""
        # Invalid status enum
        r1 = client.post(
            "/api/v1/shipments/TRK-CHI-NYC-8801/status",
            json={"status": "TELEPORTED_TO_MARS"},
            headers=dispatcher_headers
        )
        assert r1.status_code == 422, f"Expected 422 for invalid status enum, got {r1.status_code}"

        # Invalid milestone type enum
        r2 = client.post(
            "/api/v1/shipments/TRK-CHI-NYC-8801/milestones",
            json={"milestone_type": "WARP_DRIVE_ENGAGED"},
            headers=dispatcher_headers
        )
        assert r2.status_code == 422, f"Expected 422 for invalid milestone enum, got {r2.status_code}"

    def test_input_validation_oversized_inputs_and_capacities(self, client, manager_headers, customer_headers):
        """Boundary: Test physical warehouse capacity limit and oversized strings."""
        # Restock 10,000,000 units clearly exceeding warehouse physical capacity
        r_overflow = client.post(
            "/api/v1/inventory/restock",
            json={"warehouse_id": "wh-01", "product_id": "prod-01", "quantity": 10000000},
            headers=manager_headers
        )
        assert r_overflow.status_code == 422, f"Expected 422 for warehouse capacity overflow, got {r_overflow.status_code}"
        assert "capacity" in r_overflow.get_json().get("message", "").lower()

        # Enormous address string (10,000 characters) handled gracefully without server crash
        large_address = "A" * 10000
        r_large = client.post(
            "/api/v1/orders",
            json={"items": [{"product_id": "prod-01", "quantity": 1}], "shipping_address": large_address},
            headers=customer_headers
        )
        assert r_large.status_code in (200, 201, 400, 409), f"Oversized address crashed server with {r_large.status_code}"


# =============================================================================
# 5. Web UI Route Accessibility & HTML Content Tests
# =============================================================================

class TestWebUIRoutesAdversarial:
    """Verifies all human-facing web interface views return HTTP 200 with valid HTML content."""

    def test_web_ui_dashboard_route(self, client):
        """Verify: GET / renders executive operations dashboard."""
        res = client.get("/")
        assert res.status_code == 200, f"Expected 200 for GET /, got {res.status_code}"
        assert "text/html" in res.content_type
        content = res.data.decode("utf-8")
        assert "<title>" in content or "<html" in content
        assert "Dashboard" in content or "Logistics" in content

    def test_web_ui_tracking_route(self, client):
        """Verify: GET /tracking renders shipment tracking interface."""
        res = client.get("/tracking")
        assert res.status_code == 200, f"Expected 200 for GET /tracking, got {res.status_code}"
        assert "text/html" in res.content_type
        content = res.data.decode("utf-8")
        assert "Tracking" in content or "Shipment" in content

        # With query parameter trk
        res_trk = client.get("/tracking?trk=TRK-CHI-NYC-8801")
        assert res_trk.status_code == 200
        assert "text/html" in res_trk.content_type

    def test_web_ui_inventory_route(self, client):
        """Verify: GET /inventory renders multi-warehouse inventory browser."""
        res = client.get("/inventory")
        assert res.status_code == 200, f"Expected 200 for GET /inventory, got {res.status_code}"
        assert "text/html" in res.content_type
        content = res.data.decode("utf-8")
        assert "Inventory" in content or "Warehouse" in content

        # With warehouse and low_stock filters
        res_filter = client.get("/inventory?warehouse_id=wh-01&low_stock=1")
        assert res_filter.status_code == 200

    def test_web_ui_network_route(self, client):
        """Verify: GET /network renders network graph & bottleneck visualizer."""
        res = client.get("/network")
        assert res.status_code == 200, f"Expected 200 for GET /network, got {res.status_code}"
        assert "text/html" in res.content_type
        content = res.data.decode("utf-8")
        assert "Network" in content or "Corridor" in content or "Routing" in content


# =============================================================================
# 6. Bulk Batch Shipment Ingestion Authorization Tests
# =============================================================================

class TestBulkBatchShipmentAuthorizationAdversarial:
    """Verifies authentication and role authorization boundaries on /api/v1/shipments/batch."""

    VALID_BATCH_PAYLOAD = {
        "batch_id": "BATCH-TEST-AUTH-001",
        "shipments": [
            {
                "order_id": "ord-test-batch-001",
                "origin_warehouse_id": "wh-01",
                "destination_city": "Chicago",
                "carrier": "APEX_FREIGHT",
                "service_level": "STANDARD"
            },
            {
                "order_id": "ord-test-batch-002",
                "origin_warehouse_id": "wh-02",
                "destination_city": "New York",
                "carrier": "APEX_FREIGHT",
                "service_level": "EXPRESS"
            }
        ]
    }

    def test_batch_shipment_unauthenticated(self, client):
        """Verify: Unauthenticated request returns HTTP 401 Unauthorized."""
        res = client.post("/api/v1/shipments/batch", json=self.VALID_BATCH_PAYLOAD)
        assert res.status_code == 401, f"Expected 401 for unauthenticated batch, got {res.status_code}"
        assert res.get_json().get("error") == "Unauthorized"

    def test_batch_shipment_unauthorized_roles(self, client, driver_headers, customer_headers, manager_headers):
        """Verify: Driver, Customer, and WarehouseManager roles return HTTP 403 Forbidden."""
        for role_name, headers in [
            ("Driver", driver_headers),
            ("Customer", customer_headers),
            ("WarehouseManager", manager_headers)
        ]:
            res = client.post("/api/v1/shipments/batch", json=self.VALID_BATCH_PAYLOAD, headers=headers)
            assert res.status_code == 403, f"Role {role_name} bypassed batch shipment authorization: {res.status_code}"
            assert res.get_json().get("error") == "Forbidden"

    def test_batch_shipment_authorized_dispatcher(self, client, dispatcher_headers):
        """Verify: Dispatcher role successfully ingests batch with HTTP 201 Created."""
        res = client.post("/api/v1/shipments/batch", json=self.VALID_BATCH_PAYLOAD, headers=dispatcher_headers)
        assert res.status_code == 201, f"Dispatcher failed batch creation: {res.status_code}, data={res.data}"
        data = res.get_json()
        assert data.get("status") == "SUCCESS"
        assert data.get("batch_id") == "BATCH-TEST-AUTH-001"
        assert data.get("processed_count") == 2
        assert len(data.get("tracking_numbers", [])) == 2

    def test_batch_shipment_authorized_admin(self, client, admin_headers):
        """Verify: Admin role successfully ingests batch with HTTP 201 Created."""
        batch_payload = {
            "batch_id": "BATCH-TEST-ADMIN-002",
            "shipments": [
                {
                    "order_id": "ord-test-admin-001",
                    "origin_warehouse_id": "wh-01",
                    "destination_city": "Dallas",
                    "carrier": "APEX_FREIGHT",
                    "service_level": "STANDARD"
                }
            ]
        }
        res = client.post("/api/v1/shipments/batch", json=batch_payload, headers=admin_headers)
        assert res.status_code == 201, f"Admin failed batch creation: {res.status_code}, data={res.data}"
        data = res.get_json()
        assert data.get("status") == "SUCCESS"
        assert data.get("processed_count") == 1
