from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.core.database import create_schema


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.is_production and settings.secret_key == "local-development-only-change-me":
        raise RuntimeError("Set SECRET_KEY before starting in production.")
    if settings.is_production and not settings.is_postgresql:
        raise RuntimeError("Production requires a PostgreSQL DATABASE_URL.")
    if not settings.is_production:
        create_schema()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.exception_handler(Exception)
async def api_exception_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={
                "detail": "The Professor AI service hit an unexpected error. Please retry.",
                "error_type": exc.__class__.__name__,
            },
        )
    raise exc


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/login")
def login():
    return FileResponse(static_dir / "index.html")


@app.get("/register")
def register():
    return FileResponse(static_dir / "index.html")
