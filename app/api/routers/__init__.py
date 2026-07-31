"""API routers aggregated under :mod:`app.api.routers`."""

from app.api.routers.chat import router as chat_router
from app.api.routers.chat_supervisor import router as chat_supervisor_router
from app.api.routers.devis import router as devis_router
from app.api.routers.landing_chat import router as landing_chat_router
from app.api.routers.mobile_chat import router as mobile_chat_router
from app.api.routers.trade_line import router as trade_line_router
from app.api.routers.voice import router as voice_router
from app.api.routers.v3_quotes import router as v3_quotes_router

__all__ = [
    "chat_router",
    "chat_supervisor_router",
    "devis_router",
    "landing_chat_router",
    "mobile_chat_router",
    "trade_line_router",
    "voice_router",
    "v3_quotes_router",
]
