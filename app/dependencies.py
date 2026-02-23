"""
FastAPI dependencies — inject the shared API client via Depends().
The client instance lives on app.state, set during lifespan.
"""
from fastapi import Request
from app.api.client import ThrottledAPIClient


def get_client(request: Request) -> ThrottledAPIClient:
    return request.app.state.client
