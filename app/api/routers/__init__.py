"""API routers aggregated under :mod:`app.api.routers`."""

from app.api.routers.devis import router as devis_router
from app.api.routers.trade_line import router as trade_line_router
from app.api.routers.chat import router as chat_router

__all__ = ["devis_router", "trade_line_router", "chat_router"]
