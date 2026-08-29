"""Make the workspace importable without installing anything.

Each package under packages/ is a real distributable with its own pyproject, so
production installs them properly. This shim exists so the suite runs on a clean
checkout with nothing but the standard library — the same property the parsing
and resolution logic was written to keep.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in [ROOT, ROOT / "apps", *(ROOT / "packages").glob("*/")]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Note: the extraction modules import pydantic at module load, because the
# response models are the only route from a Claude response to a row. That
# makes `pydantic` a hard requirement of the test suite -- it used to run on a
# bare interpreter, and no longer does. Install requirements first.
