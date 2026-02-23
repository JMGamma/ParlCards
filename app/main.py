import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from app.api.client import ThrottledAPIClient
from app.cache.warmup import background_warmup
from app.routers import home, politician, api as api_router, browse as browse_router
from app.templates_config import templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create the shared API client
    client = ThrottledAPIClient()
    await client.start()
    app.state.client = client

    # Start background warmup (non-blocking)
    warmup_task = asyncio.create_task(background_warmup(client))
    app.state.warmup_task = warmup_task

    yield

    # Shutdown: cancel warmup task and close connection pool
    warmup_task.cancel()
    try:
        await warmup_task
    except asyncio.CancelledError:
        pass
    await client.stop()


app = FastAPI(title="ParlCards", lifespan=lifespan)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(home.router)
app.include_router(politician.router)
app.include_router(api_router.router)
app.include_router(browse_router.router)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "title": "Page not found",
            "detail": "That politician or page doesn't exist.",
        },
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: Exception):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "title": "Server error",
            "detail": "Something went wrong fetching parliamentary data.",
        },
        status_code=500,
    )
