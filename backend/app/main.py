from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.dashboard import router as dashboard_router

app = FastAPI(title="Razorpay Recovery Agent")
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

