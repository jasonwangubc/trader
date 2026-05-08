import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, backtest, chart, earnings, fundamentals, health, journal, monitor, options, positions, regime, screener, tickets
from app.api import settings as user_settings
from app.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.brokers.registry import get_broker
    from app.services.monitor_service import MonitorService
    from app.services.nightly_service import run_nightly_loop, startup_stale_check

    broker = get_broker()
    svc = MonitorService(broker)
    monitor.set_monitor(svc)

    monitor_task  = asyncio.create_task(svc.run())
    nightly_task  = asyncio.create_task(run_nightly_loop())
    startup_task  = asyncio.create_task(startup_stale_check())

    yield

    await svc.stop()
    for task in (monitor_task, nightly_task, startup_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


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
    app.include_router(positions.router)
    app.include_router(tickets.router)
    app.include_router(monitor.router)
    app.include_router(screener.router)
    app.include_router(journal.router)
    app.include_router(regime.router)
    app.include_router(options.router)
    app.include_router(chart.router)
    app.include_router(earnings.router)
    app.include_router(fundamentals.router)
    app.include_router(backtest.router)
    app.include_router(user_settings.router)
    return app


app = create_app()
