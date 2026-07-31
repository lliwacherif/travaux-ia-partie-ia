"""Isolated V3 quote-engine package.

Nothing in this package is imported by the V2 application at module startup.
V3 routes and services must opt in explicitly.
"""

from app.v3.ssot import SSOT_VERSION

__all__ = ["SSOT_VERSION"]
