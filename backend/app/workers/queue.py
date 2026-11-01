"""Background jobs (notifications, Excel import, PDFs) go through RQ.

Run a worker: `rq worker --url $REDIS_URL easo` (the compose `worker` service does this).
"""

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.config import get_settings

QUEUE_NAME = "easo"


@lru_cache
def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=Redis.from_url(get_settings().redis_url))
