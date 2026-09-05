import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key(api_key_header: str | None = Security(api_key_header)) -> str | None:
    """Validate the API key from the request header.

    When ``API_SECRET_KEY`` is not configured (empty string), authentication
    is skipped entirely so the dashboard works out of the box for local
    development.  When a key **is** set, every request must include a
    matching ``X-API-Key`` header.
    """
    if not settings.api_secret_key:
        # No key configured → auth is opt-in, allow the request.
        return None

    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    if not hmac.compare_digest(api_key_header, settings.api_secret_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    return api_key_header

