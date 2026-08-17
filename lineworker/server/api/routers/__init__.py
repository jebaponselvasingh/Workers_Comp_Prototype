from api.routers.admin import router as admin_router
from api.routers.auth import router as auth_router
from api.routers.claims import router as claims_router
from api.routers.diary import router as diary_router
from api.routers.glossary import router as glossary_router
from api.routers.stats import router as stats_router

__all__ = [
    "admin_router",
    "auth_router",
    "claims_router",
    "diary_router",
    "glossary_router",
    "stats_router",
]
