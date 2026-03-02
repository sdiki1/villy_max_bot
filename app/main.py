from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.bot.service import MaxBotService
from app.config import get_settings
from app.database import SessionFactory, dispose_db, init_db
from app.web.routes_admin import router as admin_router

settings = get_settings()
BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    bot_service = MaxBotService(
        token=settings.max_bot_token,
        session_factory=SessionFactory,
        skip_updates=settings.max_skip_updates,
        welcome_image_path=settings.welcome_image_path,
    )
    app.state.bot_service = bot_service

    await bot_service.start()
    try:
        yield
    finally:
        await bot_service.stop()
        await dispose_db()


app = FastAPI(
    title="VillyPrint MAX Bot",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.admin_session_secret,
    same_site="lax",
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "web" / "static")),
    name="static",
)

app.include_router(admin_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/admin/chats", status_code=302)
