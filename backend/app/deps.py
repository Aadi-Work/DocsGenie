from __future__ import annotations

from functools import lru_cache

from app.agent.orchestrator import AgentOrchestrator
from app.rag.retriever import HybridRetriever
from app.services.catalog import AccessControl, CatalogService
from app.services.doc_generator import DocumentGenerator
from app.services.ingest import DocumentIngestService
from app.services.versioning import VersionService
from app.services.auth import AuthService


@lru_cache
def get_catalog() -> CatalogService:
    return CatalogService()


@lru_cache
def get_acl() -> AccessControl:
    return AccessControl()


@lru_cache
def get_retriever() -> HybridRetriever:
    return HybridRetriever(get_catalog())


@lru_cache
def get_generator() -> DocumentGenerator:
    return DocumentGenerator(get_catalog())


@lru_cache
def get_ingest() -> DocumentIngestService:
    return DocumentIngestService(
        catalog=get_catalog(),
        retriever=get_retriever(),
        acl=get_acl(),
        generator=get_generator(),
    )


@lru_cache
def get_versions() -> VersionService:
    return VersionService(get_catalog())


@lru_cache
def get_auth() -> AuthService:
    return AuthService()


@lru_cache
def get_agent() -> AgentOrchestrator:
    return AgentOrchestrator(
        catalog=get_catalog(),
        retriever=get_retriever(),
        acl=get_acl(),
        generator=get_generator(),
    )
