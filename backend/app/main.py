from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="Razorpay Recovery Agent")
app.state.settings = settings


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
