from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import dashboard, demo, doctors, handoffs, payments, queue, schedules, staff
from app.core.config import get_settings


app = FastAPI(title="Marina Smile AI Receptionist")
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
