import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import dashboard, demo, doctors, handoffs, payments, queue, schedules, staff
from app.core.config import get_settings


load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    telegram_application = None
    holiday_sync_task = None
    app_settings = get_settings()
    if app_settings.holiday_sync_enabled:
        from app.operations.holiday_sync import run_holiday_sync_scheduler

        holiday_sync_task = asyncio.create_task(run_holiday_sync_scheduler())
        logger.info("UAE holiday sync scheduler started")
    else:
        logger.info("UAE holiday sync scheduler disabled")

    if os.getenv("TELEGRAM_BOT_TOKEN"):
        from app.telegram.bot import start_polling_bot, stop_polling_bot

        try:
            telegram_application = await start_polling_bot()
        except Exception:
            logger.exception("Telegram failed to start; continuing with the API")
    else:
        logger.info("TELEGRAM_BOT_TOKEN is unset; starting API without Telegram polling")

    try:
        yield
    finally:
        if holiday_sync_task is not None:
            holiday_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await holiday_sync_task
        if telegram_application is not None:
            await stop_polling_bot(telegram_application)


app = FastAPI(title="Marina Smile AI Receptionist", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.dashboard_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(staff.router)
app.include_router(demo.router)
app.include_router(dashboard.router)
app.include_router(queue.router)
app.include_router(payments.router)
app.include_router(doctors.router)
app.include_router(schedules.router)
app.include_router(handoffs.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "Marina Smile AI Receptionist",
    }
