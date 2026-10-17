import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .ai.agent import Agent
from .ai.client import create_async_client
from .ai.knowledge import KnowledgeCache, count_tokens_forever
from .chat.hub import Hub
from .chat.router import router as chat_router
from .chat.service import ChatService
from .config import get_settings
from .db.engine import engine
from .redis_client import create_redis
from .routers import catalog

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = create_redis()
    hub = Hub()
    client = create_async_client(settings)
    agent = Agent(client, settings, redis, hub, KnowledgeCache())
    app.state.chat = ChatService(redis, hub, settings, agent)
    background = [asyncio.create_task(app.state.chat.listen())]
    if settings.anthropic_api_key:
        # Размер версий базы знаний в токенах — для админки (ТЗ §9)
        background.append(asyncio.create_task(count_tokens_forever(client, settings.anthropic_model)))
    else:
        logger.warning("ANTHROPIC_API_KEY не задан: агент будет отвечать ошибкой «модель недоступна»")
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await hub.shutdown()
        await client.close()
        await redis.aclose()
        await engine.dispose()


# Документация API доступна только при DEBUG=True
app = FastAPI(
    title=settings.app_title,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url="/api/redoc" if settings.debug else None,
    openapi_url="/api/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.site_origin],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(catalog.router)
app.include_router(chat_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
