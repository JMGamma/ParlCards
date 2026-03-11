#!/usr/bin/env python3
"""
ParlCards static site build script.

Fetches API data, computes metrics for all MPs, and renders the full dist/
folder of static HTML files deployable to Cloudflare Pages or GitHub Pages.

Usage:
  python build.py                        # full build
  python build.py --mp burton-bailey     # single MP (fast iteration)
  python build.py --skip-fetch           # skip API fetch, use existing cache
  python build.py --session 44-1         # different session
"""
import argparse
import asyncio
import json
import logging
import math
import shutil
import sys
from pathlib import Path

import jinja2

from app.api.client import ThrottledAPIClient
from app.api.politicians import fetch_politician_list
from app.api.votes import fetch_session_votes, fetch_politician_ballots
from app.api.speeches import fetch_politician_speeches
from app.api.bills import fetch_sponsored_bills
from app.config import settings
from app.metrics.percentiles import (
    build_rankings_table,
    load_or_build_rankings,
    compute_card_metrics_for_mp,
    compute_all_groups_for_mp,
    cache_static_card_for_mp,
    load_static_card_for_mp,
    get_percentile,
    bar_color,
    ordinal,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("parlcards.build")

DIST = Path("dist")
PAGE_SIZE = 25

METRICS = {
    "attendance":      {"label": "Voting Attendance",    "short": "Attendance",    "unit": "%",         "decimals": 1},
    "party_loyalty":   {"label": "Party Loyalty",        "short": "Party Loyalty", "unit": "%",         "decimals": 1},
    "bills_sponsored": {"label": "Bills Sponsored",      "short": "Bills",         "unit": "",          "decimals": 0},
    "debate_speeches": {"label": "Debate Participation", "short": "Debate",        "unit": " speeches", "decimals": 0},
}

# Passed to Jinja2 as a global so card.html can render toggle buttons.
# To add a new metric: add it here and to the metrics computation layer.
METRIC_REGISTRY = [
    {"key": k, "label": v["label"], "short": v["short"]}
    for k, v in METRICS.items()
]


# ---------------------------------------------------------------------------
# Jinja2 setup
# ---------------------------------------------------------------------------

def setup_jinja_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader("app/templates"),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    env.globals["bar_color"] = bar_color
    env.globals["ordinal"] = ordinal
    env.globals["metric_registry"] = METRIC_REGISTRY
    return env


def _write(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Browse helpers (mirrors app/routers/browse.py)
# ---------------------------------------------------------------------------

def _build_rows(rankings_table: dict, pol_map: dict, metric: str, group: str) -> list[dict]:
    metrics_list = rankings_table.get("metrics", [])
    government_party = settings.government_party

    if group == "government":
        filtered = [m for m in metrics_list if m.get("party") == government_party]
    elif group == "opposition":
        filtered = [m for m in metrics_list if m.get("party") != government_party]
    else:
        filtered = metrics_list

    sorted_metrics = sorted(
        filtered,
        key=lambda m: (m.get(metric) is not None, m.get(metric) or 0),
        reverse=True,
    )
    all_vals = [m[metric] for m in filtered if m.get(metric) is not None]

    rows = []
    for rank_idx, m in enumerate(sorted_metrics):
        pol = pol_map.get(m["slug"], {})
        val = m.get(metric)
        pct = get_percentile(val, all_vals) if val is not None else None
        rows.append({
            "rank":       rank_idx + 1,
            "slug":       m["slug"],
            "name":       pol.get("name", m["slug"]),
            "party":      pol.get("party", m.get("party", "")),
            "party_slug": pol.get("party_slug", "independent"),
            "riding":     pol.get("riding", ""),
            "province":   pol.get("province", ""),
            "image":      pol.get("image", ""),
            "value":      val,
            "percentile": pct,
        })
    return rows


def _paginate(rows: list, page: int) -> tuple[list, int, int, int]:
    total = len(rows)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    return rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], page, total_pages, total


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def render_home(env: jinja2.Environment, session: str) -> None:
    html = env.get_template("home.html").render(available_sessions=[session])
    _write(DIST / "index.html", html)
    log.info("  home → dist/index.html")


def render_404(env: jinja2.Environment) -> None:
    html = env.get_template("error.html").render(
        status_code=404,
        title="Page not found",
        detail="That politician or page doesn't exist.",
    )
    _write(DIST / "404.html", html)
    log.info("  404  → dist/404.html")


def render_politician(
    env: jinja2.Environment,
    politician: dict,
    card: dict,
    session: str,
) -> None:
    slug = politician["slug"]
    html = env.get_template("politician.html").render(
        politician=politician,
        slug=slug,
        session=session,
        # Card data for inline rendering
        metrics=card["metrics"],
        percentiles=card["by_group"]["all"]["percentiles"],
        distributions=card["by_group"]["all"]["distributions"],
        active_group="all",
        rankings_available=True,
        # JSON embed for Phase 3 JS group switching
        by_group_data=card["by_group"],
    )
    _write(DIST / "politicians" / slug / "index.html", html)


def render_browse_pages(
    env: jinja2.Environment,
    rankings_table: dict,
    pol_map: dict,
    session: str,
) -> None:
    template = env.get_template("browse.html")
    total_files = 0

    for metric in METRICS:
        for group in ("all", "government", "opposition"):
            rows = _build_rows(rankings_table, pol_map, metric, group)
            _, _, total_pages, _ = _paginate(rows, 1)

            for page_num in range(1, total_pages + 1):
                page_rows, page, tp, total = _paginate(rows, page_num)
                html = template.render(
                    metric=metric,
                    metric_meta=METRICS[metric],
                    metrics_list=METRICS,
                    group=group,
                    page=page,
                    total_pages=tp,
                    total=total,
                    rows=page_rows,
                    rankings_available=True,
                    session=session,
                )
                _write(DIST / "browse" / metric / group / str(page_num) / "index.html", html)
                total_files += 1

    # /browse/ redirect to default view
    redirect_html = (
        '<!DOCTYPE html><html><head>'
        '<meta http-equiv="refresh" content="0;url=/browse/attendance/all/1/">'
        '</head></html>'
    )
    _write(DIST / "browse" / "index.html", redirect_html)
    log.info("  browse → %d pages + redirect", total_files)


def render_all(
    env: jinja2.Environment,
    target_list: list[dict],
    rankings_table: dict,
    all_politicians: list[dict],
    session: str,
    target_mp: str | None,
) -> None:
    pol_map = {p["slug"]: p for p in all_politicians}

    if not target_mp:
        render_home(env, session)
        render_404(env)

    log.info("Rendering %d politician pages...", len(target_list))
    rendered = 0
    for politician in target_list:
        slug = politician["slug"]
        card = load_static_card_for_mp(slug, session)
        if card is None:
            log.warning("  No static card for %s — skipping HTML", slug)
            continue
        render_politician(env, politician, card, session)
        rendered += 1

    log.info("  politicians → %d pages", rendered)

    if not target_mp:
        render_browse_pages(env, rankings_table, pol_map, session)
        copy_static_assets()
        write_search_index(all_politicians)


def copy_static_assets() -> None:
    src = Path("app/static")
    dst = DIST / "static"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    log.info("  static assets copied → dist/static/")


def write_search_index(politicians: list[dict]) -> None:
    index = [
        {
            "slug":     p["slug"],
            "name":     p["name"],
            "riding":   p.get("riding", ""),
            "province": p.get("province", ""),
            "party":      p.get("party", ""),
            "party_slug": p.get("party_slug", "independent"),
            "photo_url":  p.get("image", ""),
        }
        for p in politicians
    ]
    out = DIST / "static" / "politicians.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    log.info("  search index → dist/static/politicians.json (%d MPs)", len(index))


# ---------------------------------------------------------------------------
# Data pipeline
# ---------------------------------------------------------------------------

async def fetch_raw_data(
    client: ThrottledAPIClient,
    politicians: list[dict],
    session: str,
) -> tuple[int, int]:
    ok = 0
    failed = 0
    total = len(politicians)
    for i, politician in enumerate(politicians):
        slug = politician["slug"]
        try:
            await asyncio.gather(
                fetch_politician_ballots(client, slug, session),
                fetch_politician_speeches(client, slug, session),
                fetch_sponsored_bills(client, slug, session),
            )
            ok += 1
        except Exception as e:
            log.warning("Failed to fetch %s: %s", slug, e)
            failed += 1

        if (i + 1) % 25 == 0 or (i + 1) == total:
            log.info("  Fetch progress: %d/%d (%d failed)", i + 1, total, failed)

    return ok, failed


async def build(session: str, target_mp: str | None, skip_fetch: bool) -> None:
    client = ThrottledAPIClient()
    await client.start()
    try:
        await _build(client, session, target_mp, skip_fetch)
    finally:
        await client.stop()


async def _build(
    client: ThrottledAPIClient,
    session: str,
    target_mp: str | None,
    skip_fetch: bool,
) -> None:
    # --- Politician list -------------------------------------------------------
    all_politicians = await fetch_politician_list(client)
    log.info("Politician list: %d MPs", len(all_politicians))

    if target_mp:
        target_list = [p for p in all_politicians if p["slug"] == target_mp]
        if not target_list:
            log.error("MP '%s' not found in politician list", target_mp)
            sys.exit(1)
        log.info("Single-MP mode: %s", target_mp)
    else:
        target_list = all_politicians

    # --- Session votes (shared) -----------------------------------------------
    session_votes = await fetch_session_votes(client, session)
    log.info("Session votes: %d", len(session_votes))

    # --- Phase 1: Raw data fetch ----------------------------------------------
    if skip_fetch:
        log.info("Skipping raw data fetch (--skip-fetch)")
    else:
        log.info("Fetching raw data for %d MPs...", len(target_list))
        ok, failed = await fetch_raw_data(client, target_list, session)
        log.info("Raw fetch complete: %d ok, %d failed", ok, failed)

    # --- Phase 2: Rankings table ----------------------------------------------
    if target_mp:
        rankings_table = await load_or_build_rankings(client, session)
        if rankings_table is None:
            log.info("No cached rankings table — building now...")
            rankings_table = await build_rankings_table(client, session)
        else:
            log.info("Loaded rankings table from cache (%d MPs)", len(rankings_table.get("metrics", [])))
    else:
        log.info("Building rankings table...")
        rankings_table = await build_rankings_table(client, session)
        log.info("Rankings table built: %d MPs", len(rankings_table.get("metrics", [])))

    # --- Phase 3: Compute and cache static cards ------------------------------
    log.info("Computing static cards for %d MPs...", len(target_list))
    built = 0
    failed_cards = 0

    for politician in target_list:
        slug = politician["slug"]
        try:
            card = await compute_card_metrics_for_mp(
                client, slug, session, session_votes, politician, rankings_table
            )
            if card is None:
                log.warning("  No card data for %s — skipping", slug)
                failed_cards += 1
                continue

            card["by_group"] = compute_all_groups_for_mp(
                slug,
                rankings_table,
                politician.get("party", ""),
                settings.government_party,
            )
            cache_static_card_for_mp(slug, session, card)
            built += 1

        except Exception as e:
            log.warning("  Failed %s: %s", slug, e)
            failed_cards += 1

    log.info("Static cards: %d built, %d failed", built, failed_cards)

    # --- Phase 4: Render HTML -------------------------------------------------
    log.info("Rendering HTML...")
    if not target_mp:
        if DIST.exists():
            shutil.rmtree(DIST)
        DIST.mkdir()

    env = setup_jinja_env()
    render_all(env, target_list, rankings_table, all_politicians, session, target_mp)
    log.info("Build complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ParlCards static site build script")
    parser.add_argument(
        "--session",
        default=settings.session,
        help="Parliamentary session (default: %(default)s)",
    )
    parser.add_argument(
        "--mp",
        metavar="SLUG",
        help="Build only this MP slug (for testing, e.g. --mp burton-bailey)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip raw API data fetch and use existing cache only",
    )
    args = parser.parse_args()

    asyncio.run(build(args.session, target_mp=args.mp, skip_fetch=args.skip_fetch))


if __name__ == "__main__":
    main()
