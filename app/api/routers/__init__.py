"""API routers aggregated under :mod:`app.api.routers`."""

from app.api.routers.devis import router as devis_router
from app.api.routers.trade_line import router as trade_line_router
from app.api.routers.chat import router as chat_router
from app.api.routers.landing_chat import router as landing_chat_router
from app.api.routers.mobile_chat import router as mobile_chat_router
from app.api.routers.voice import router as voice_router

__all__ = [
    "devis_router",
    "trade_line_router",
    "chat_router",
    "landing_chat_router",
    "mobile_chat_router",
    "voice_router",
]
