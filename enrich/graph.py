"""Build + compile the per-domain StateGraph and its routing functions.

Pipeline shape and conditional-edge rules: docs/ARCHITECTURE.md. Verified LangGraph API
(`StateGraph`, `START`/`END`, `.compile().ainvoke`) in docs/references/stack.md.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from enrich.cleaner import clean_pages
from enrich.discovery import discover_links
from enrich.extractor import extract
from enrich.fetcher import fetch_home, fetch_subpages
from enrich.navigator import agentic_navigate
from enrich.scoring import score
from enrich.search import search_linkedin
from enrich.state import DomainState
from enrich.verify import verify


def route_after_fetch_home(state: DomainState) -> str:
    """score (skipping discovery/extraction entirely) on a hard `fetch_home` failure
    (DNS error, non-2xx after retries, bot wall with no content) — never call the LLM
    on nothing; else discover_links."""
    raise NotImplementedError


def route_after_discover(state: DomainState) -> str:
    """agentic_navigate if BROWSER_USE_ENABLED and no team/about/leadership page found,
    or homepage cleaned text < 800 chars; else straight to fetch_subpages."""
    raise NotImplementedError


def route_after_verify(state: DomainState) -> str:
    """search_linkedin if any verified leader lacks a LinkedIn URL (or none survived
    verification) and a Tavily key is configured; else straight to score."""
    raise NotImplementedError


async def finalize(state: DomainState) -> dict:
    """Node: apply the ok/partial/failed status rule and assemble the final result."""
    raise NotImplementedError


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(DomainState)

    graph.add_node("fetch_home", fetch_home)
    graph.add_node("discover_links", discover_links)
    graph.add_node("agentic_navigate", agentic_navigate)
    graph.add_node("fetch_subpages", fetch_subpages)
    graph.add_node("clean_pages", clean_pages)
    graph.add_node("extract", extract)
    graph.add_node("verify", verify)
    graph.add_node("search_linkedin", search_linkedin)
    graph.add_node("score", score)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "fetch_home")
    graph.add_conditional_edges(
        "fetch_home",
        route_after_fetch_home,
        {"discover_links": "discover_links", "score": "score"},
    )
    graph.add_conditional_edges(
        "discover_links",
        route_after_discover,
        {"agentic_navigate": "agentic_navigate", "fetch_subpages": "fetch_subpages"},
    )
    graph.add_edge("agentic_navigate", "fetch_subpages")
    graph.add_edge("fetch_subpages", "clean_pages")
    graph.add_edge("clean_pages", "extract")
    graph.add_edge("extract", "verify")
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        {"search_linkedin": "search_linkedin", "score": "score"},
    )
    graph.add_edge("search_linkedin", "score")
    graph.add_edge("score", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
