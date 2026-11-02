"""The one error type services raise for a request they cannot serve.

It carries the HTTP status (and optional headers) so the API maps it 1:1 to an HTTPException
(`api.deps.service_errors`); the CLI prints the message. Services never import FastAPI."""

from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers
