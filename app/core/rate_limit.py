import hashlib
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client


_INCREMENT_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def rate_limit(
    scope: str,
    limit_setting: str,
) -> Callable[[Request], None]:
    def enforce_rate_limit(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return

        limit = getattr(settings, limit_setting)
        window = settings.rate_limit_window_seconds
        client_host = (
            request.client.host if request.client is not None else "unknown"
        )
        client_key = hashlib.sha256(
            client_host.encode("utf-8")
        ).hexdigest()
        window_start = int(time.time()) // window
        redis_key = f"rate-limit:{scope}:{client_key}:{window_start}"

        try:
            count = get_redis_client().eval(
                _INCREMENT_WITH_TTL,
                1,
                redis_key,
                window,
            )
        except RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting is temporarily unavailable.",
                headers={"Retry-After": str(window)},
            ) from exc

        if count > limit:
            seconds_remaining = window - int(time.time()) % window
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(seconds_remaining)},
            )

    return enforce_rate_limit
