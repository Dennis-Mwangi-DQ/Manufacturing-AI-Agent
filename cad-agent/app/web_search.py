"""
Web search for engineering context — Tavily API.

Used during the second LLM enrichment pass when the first pass returns
low-confidence material or part-type classification.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def build_material_search_query(raw: dict, llm_data: Optional[dict] = None) -> str:
    """Build a focused search query from part metadata and first-pass LLM output."""
    terms: list[str] = []

    part_name = raw.get("part_name") or raw.get("part_id")
    if part_name:
        terms.append(str(part_name))

    fmeta = raw.get("filename_meta") or {}
    if fmeta.get("raw_code"):
        terms.append(str(fmeta["raw_code"]))
    if fmeta.get("part_number"):
        terms.append(str(fmeta["part_number"]))

    if llm_data:
        if llm_data.get("part_type"):
            terms.append(str(llm_data["part_type"]).replace("_", " ").lower())
        if llm_data.get("material"):
            terms.append(str(llm_data["material"]))

    thickness = raw.get("thickness_mm")
    if thickness is not None:
        terms.append(f"{thickness}mm sheet metal")

    shape = raw.get("shape_type")
    if shape and shape != "UNKNOWN":
        terms.append(str(shape).replace("_", " ").lower())

    terms.append("manufacturing material specification")
    return " ".join(terms)


def format_search_results(results: list[dict]) -> str:
    """Format Tavily hits as plain text for the retry LLM prompt."""
    if not results:
        return "No web search results available."

    lines: list[str] = []
    for i, result in enumerate(results, 1):
        title = result.get("title") or "Untitled"
        content = (result.get("content") or "").strip()
        url = result.get("url") or ""
        lines.append(f"{i}. {title}\n   {content}\n   Source: {url}")
    return "\n".join(lines)


def search_engineering_context(
    query: str,
    api_key: str,
    max_results: int = 3,
) -> list[dict]:
    """
    Query Tavily for engineering/material context.

    Returns a list of {title, content, url}. Returns [] when the API key is
    missing, the query is empty, or the request fails.
    """
    if not api_key or not query.strip():
        return []

    try:
        import httpx

        response = httpx.post(
            TAVILY_SEARCH_URL,
            json={
                "api_key": api_key,
                "query": query.strip(),
                "search_depth": "basic",
                "max_results": max_results,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        return [
            {
                "title": hit.get("title", ""),
                "content": hit.get("content", ""),
                "url": hit.get("url", ""),
            }
            for hit in payload.get("results", [])
        ]
    except Exception as exc:
        logger.warning("Web search failed for query %r: %s", query, exc)
        return []
