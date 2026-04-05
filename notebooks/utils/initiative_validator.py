"""
Wrapper module so notebooks can import initiative validation from utils:

    from utils.initiative_validator import run_initiative_validation, export_combined_results, ...

The implementation lives in `notebooks/initiative_validator.py` (one level up).
"""

import importlib
import sys
from pathlib import Path

# Resolve the parent directory (notebooks/) to import initiative_validator
_notebooks_dir = str(Path(__file__).resolve().parent.parent)
if _notebooks_dir not in sys.path:
    sys.path.insert(0, _notebooks_dir)

_mod = importlib.import_module("initiative_validator")
# Important: callers often reload *this* wrapper module inside notebooks.
# Without reloading the underlying implementation module too, a running
# kernel can keep using a stale InitiativeMetrics schema.
_mod = importlib.reload(_mod)

# Re-export all public names
from initiative_validator import *  # noqa: F403

# Ensure __all__ propagates if defined
if hasattr(_mod, "__all__"):
    __all__ = _mod.__all__
