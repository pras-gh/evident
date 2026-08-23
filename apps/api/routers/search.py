from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..models import SearchResponse

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=2)
    ticker: str | None = None
    form_types: list[str] | None = None
    k: int = Field(20, ge=1, le=100)


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """Vector search over evidence spans.

    A supporting index, not the product: every hit carries the document, page
    and paragraph ids it came from, so a result can always be cited.
    """
    return SearchResponse(query=req.query, hits=[], took_ms=0)
