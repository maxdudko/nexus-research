"""Versioned API routes."""

from fastapi import APIRouter

from nexus.api.v1.health import router as health_router
from nexus.api.v1.research import router as research_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(research_router, prefix="/research", tags=["research"])
