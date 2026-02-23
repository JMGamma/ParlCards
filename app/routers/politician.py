from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_detail
from app.config import settings
from app.dependencies import get_client
from app.templates_config import templates

router = APIRouter()


@router.get("/politicians/{slug}", response_class=HTMLResponse)
async def politician_page(
    slug: str,
    request: Request,
    client: ThrottledAPIClient = Depends(get_client),
):
    politician = await fetch_politician_detail(client, slug)
    if politician is None:
        raise HTTPException(status_code=404, detail="Politician not found")

    return templates.TemplateResponse(
        "politician.html",
        {
            "request": request,
            "politician": politician,
            "slug": slug,
            "session": settings.session,
        },
    )
