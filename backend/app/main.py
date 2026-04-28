from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, health
from app.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup hooks (monitor task spawn, broker session warmup) will live here.
    yield
    # Shutdown hooks (graceful broker disconnect, etc.).


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="trader",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    # CORS for local dev frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"] if settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(accounts.router)
    return app


app = create_app()
