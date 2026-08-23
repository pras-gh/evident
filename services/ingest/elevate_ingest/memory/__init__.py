"""Layer 2 — company memory.

Layer 1 (`elevate_ingest.parse_*`) stores what was filed. This stores what we
know: resolved, typed entities with a time axis, accumulated across every
filing a company has ever made.
"""
from .entities import CompanyMemory  # noqa: F401
