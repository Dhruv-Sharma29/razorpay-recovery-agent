import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.dashboard import router as dashboard_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: the append-only audit store is initialized (tables ensured) when
    # the dashboard module is imported; surface the resolved database on boot.
    from app.dashboard import _audit_logger

    logger.info("Audit store ready at %s", _audit_logger.database_url)
    yield
    # Shutdown: nothing destructive — the shared connection is reused across
    # requests for the life of the process.


app = FastAPI(title="Razorpay Recovery Agent", lifespan=lifespan)
app.state.settings = settings

# CORS: restrict to the configured frontend origins. A wildcard is not used
# because "*" is invalid alongside allow_credentials=True (browsers reject it),
# and it would expose the API to any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

