"""FastAPI entry point: `uv run uvicorn app.main:app --reload`."""

from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="FitnessBuddy", version="0.1.0")
app.include_router(router)
