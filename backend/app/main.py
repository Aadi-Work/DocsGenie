from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.ai_routes import router as ai_router
from app.api.auth_routes import router as auth_router
from app.api.document_routes import router as document_router
from app.api.template_routes import router as template_router
from app.config import get_settings
from app.services.s3_service import get_s3
from app.utils.file_utils import AppError

log = logging.getLogger("ymsli")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title=settings.app_name, version="2.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(document_router)
    app.include_router(template_router)
    app.include_router(ai_router)

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(HTTPException)
    async def http_handler(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    @app.get("/api/health")
    def api_health():
        return {"status": "healthy", "service": settings.app_name}

    @app.get("/health/aws")
    def health_aws():
        result = get_s3().health()
        if result.get("ok"):
            return {"status": "healthy", "bucket": result.get("bucket")}
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "bucket": result.get("bucket"), "detail": "S3 is unreachable"},
        )

    @app.get("/")
    def root():
        return {
            "service": settings.app_name,
            "status": "ok",
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
