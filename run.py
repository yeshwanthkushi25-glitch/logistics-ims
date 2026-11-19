import os
import sys
import time
import threading
import webbrowser
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def setup_environment():
    print("=" * 72)
    print("   LOGISTICS IMS — MULTI-MODEL DATABASE & LOGISTICS PLATFORM")
    print("=" * 72)

    # 1. Check & Seed SQLite Database if needed
    db_file = PROJECT_ROOT / "data" / "logistics.db"
    if not db_file.exists() or db_file.stat().st_size == 0:
        print("[*] Initializing SQLite WAL Database & Seed Data...")
        try:
            from backend.db.init_db import initialize_database
        except ImportError:
            from app.db.init_db import initialize_database
        initialize_database(seed=True, db_path=str(db_file))
        print("[✓] SQLite WAL Database initialized and seeded at data/logistics.db")
    else:
        print(f"[✓] SQLite WAL Database ready at data/logistics.db ({db_file.stat().st_size:,} bytes)")

    # 2. Check frontend templates & static assets
    frontend_dir = PROJECT_ROOT / "frontend"
    templates_dir = frontend_dir / "templates"
    static_dir = frontend_dir / "static"
    if templates_dir.exists() and static_dir.exists():
        print("[✓] Frontend web assets verified in frontend/templates and frontend/static")
    else:
        print("[!] Warning: Frontend directories missing, using fallback templates")

    # 3. Check documentation and presentation deck
    ppt_file = PROJECT_ROOT / "docs" / "Logistics_Management_System_Presentation.pptx"
    if ppt_file.exists():
        print(f"[✓] 27-Slide Academic Presentation deck verified in docs/ ({ppt_file.stat().st_size:,} bytes)")
    else:
        print("[!] Note: Presentation PPTX not found in docs/")

def print_welcome_banner(host="127.0.0.1", port=5000):
    print("-" * 72)
    print("🚀 LOGISTICS IMS SERVER READY!")
    print(f"📍 URL: http://{host}:{port}")
    print("-" * 72)
    print("🔑 DEMO LOGIN CREDENTIALS:")
    print("   • System Admin:       admin_user   /  admin123")
    print("   • Warehouse Manager:  wh_manager   /  whpass123")
    print("   • Dispatcher:         dispatcher_1 /  dispatch123")
    print("   • Delivery Driver:    driver_mike  /  driverpass123")
    print("   • Customer:           cust_sarah   /  custpass123")
    print("-" * 72)
    print("⚡ 5 CORE PLATFORM MODULES READY:")
    print(f"   1. Executive Dashboard:      http://{host}:{port}/")
    print(f"   2. Real-Time Tracking (FSM): http://{host}:{port}/tracking")
    print(f"   3. Inventory Browser (ROP):  http://{host}:{port}/inventory")
    print(f"   4. Highway Network (Graph):  http://{host}:{port}/network")
    print(f"   5. Service Health Probe:     http://{host}:{port}/health")
    print("-" * 72)
    print("Press CTRL+C anytime to stop the server.\n")

def start_server():
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Open browser automatically after 1.5 seconds in background
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()

    print_welcome_banner(host, port)

    try:
        from backend import create_app
    except ImportError:
        from app import create_app

    app = create_app()
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    setup_environment()
    start_server()
