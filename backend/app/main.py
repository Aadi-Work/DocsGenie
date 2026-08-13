from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_routes import router as auth_router
from app.api.onedrive_routes import router as onedrive_router
from app.api.routes import router
from app.config import get_settings
from app.deps import get_auth, get_catalog, get_retriever
from app.services.onedrive import OneDriveService


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    Path(settings.storage_path).mkdir(parents=True, exist_ok=True)
    Path(settings.storage_path, "generated").mkdir(parents=True, exist_ok=True)
    catalog = get_catalog()
    catalog.seed_from_json(force=False)
    get_retriever().reindex()
    # Initialize OneDrive service (seeds mock drive when GRAPH_MODE=mock)
    OneDriveService()
    # Seed demo email/password users
    get_auth()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(onedrive_router)
    return app


app = create_app()
