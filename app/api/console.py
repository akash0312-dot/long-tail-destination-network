"""Demo console: one static page that drives the API with the token the presenter types in. Holds no data itself."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
PAGE = Path(__file__).resolve().parent.parent / "static" / "console.html"


@router.get("/")
def root():
    return RedirectResponse("/console")


@router.get("/console")
def console():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))
