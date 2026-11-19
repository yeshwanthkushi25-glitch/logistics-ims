import sys
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent / "Logistics_Management_System"
os.chdir(ROOT_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if __name__ == "__main__":
    import runpy
    runpy.run_path(str(ROOT_DIR / "run.py"), run_name="__main__")
