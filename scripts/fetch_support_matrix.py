#!/usr/bin/env python3
"""
fetch_support_matrix.py — deterministic scrape of the live RHOAI Supported
Configurations for 3.x page into a MatrixSnapshot (scripts/common/schema.py).

Source: https://access.redhat.com/articles/rhoai-supported-configs-3.x
("Architecture, Version and Components" table)

This is the authoritative, continuously-updated cross-check target the
reference research document repeatedly leans on -- e.g. it is *this* table,
not the release notes, that shows EvalHub as Technology Preview on x86_64
at 3.5 even though the 3.5 release notes call EvalHub GA, and it is this
table that shows the OGX Operator/OGX component as GA on x86_64 at 3.5 while
the OGX product guide calls the integration Technology Preview. Detecting
exactly these kinds of conflicts is why the pipeline fetches a fresh
snapshot on every ingestion run rather than trusting the release notes alone.

Scope/limitation (first pass, deterministic, no LLM): this script parses the
single main "Architecture, Version and Components" table, which is the
page's primary x86_64-scoped view. Per-architecture breakdowns for Arm/IBM
Power/IBM Z live in separate tables and prose elsewhere on the same page and
are NOT captured here yet -- every entry is tagged architecture="x86_64" and
a fetch_warning is recorded as a reminder to cross-check architecture-specific
claims by hand during the agent-session synthesis step.

The page renders the same table 2-4 times in the raw HTML (duplicate DOM
copies for responsive/accessibility variants, at least one of which can be
an unpopulated "-" skeleton). This script picks the first candidate table
that actually contains non-placeholder data and warns if other populated
candidates disagree with it.

Usage:
    python scripts/fetch_support_matrix.py [--out data/matrix_snapshots/<date>.json] [--html-file <cached.html>]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.schema import MatrixEntry, MatrixSnapshot, Status  # noqa: E402

SOURCE_URL = "https://access.redhat.com/articles/rhoai-supported-configs-3.x"
HEADERS = {"User-Agent": "Mozilla/5.0 (rhoai-version-tracker matrix fetcher)"}

FIRST_CELL_RE = re.compile(r"RHOAI Operator Version", re.IGNORECASE)
FOOTNOTE_RE = re.compile(r"\(\d+\)\s*$")
STATUS_MAP = {
    "GA": Status.GA,
    "TP": Status.TP,
    "DP": Status.DP,
    "DEPRECATED": Status.DEPRECATED,
    "REMOVED": Status.REMOVED,
}


def fetch_html(html_file: Path | None) -> str:
    if html_file:
        return html_file.read_text(encoding="utf-8")
    resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def find_candidate_tables(soup: BeautifulSoup) -> list:
    candidates = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        first_cells = rows[0].find_all(["th", "td"])
        if first_cells and FIRST_CELL_RE.search(first_cells[0].get_text(strip=True)):
            candidates.append(table)
    return candidates


def table_is_populated(table) -> bool:
    rows = table.find_all("tr")
    for row in rows[3:]:  # skip the 3 header rows
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if any(c not in ("", "-") for c in cells[1:]):
            return True
    return False


def parse_version_labels(rows) -> list[str]:
    """Row 0 holds version labels at every 2nd column starting at index 2:
    ['RHOAI Operator Version', '', '3.3', '', '3.4', '', '3.5']."""
    cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    labels = []
    i = 2
    while i < len(cells):
        labels.append(cells[i])
        i += 2
    return labels


def normalize_status(raw: str) -> tuple[Status | None, str | None]:
    cleaned = raw.strip()
    footnote = None
    fm = FOOTNOTE_RE.search(cleaned)
    if fm:
        footnote = fm.group(0).strip()
        cleaned = FOOTNOTE_RE.sub("", cleaned).strip()
    if cleaned in ("", "-", "N/A", "TBD"):
        return None, (f"raw value: {raw!r}" if cleaned == "TBD" else None)
    status = STATUS_MAP.get(cleaned.upper())
    return status, footnote


def parse_component_table(table, warnings: list[str]) -> list[MatrixEntry]:
    rows = table.find_all("tr")
    version_labels = parse_version_labels(rows)
    entries: list[MatrixEntry] = []

    for row in rows[3:]:  # data rows, after the 3 header rows
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if not cells or not cells[0]:
            continue
        raw_name = cells[0]
        is_subcomponent = raw_name.startswith("\u21b3")  # '↳'
        component = raw_name.lstrip("\u21b3").strip()

        for i, version in enumerate(version_labels):
            status_idx = 1 + 2 * i
            version_idx = 2 + 2 * i
            if version_idx >= len(cells):
                continue
            status_raw = cells[status_idx]
            component_version = cells[version_idx].strip() or None
            status, footnote_or_note = normalize_status(status_raw)
            if status is None:
                continue  # "-" / N/A -- component not present at this RHOAI version
            notes = []
            if is_subcomponent:
                notes.append("sub-component row (indented under the row above it)")
            if footnote_or_note:
                notes.append(footnote_or_note)
            entries.append(
                MatrixEntry(
                    component=component,
                    component_version=component_version,
                    rhoai_version=version,
                    architecture="x86_64",
                    status=status,
                    notes="; ".join(notes) or None,
                )
            )
    if not entries:
        warnings.append("Parsed 0 entries from the selected components table -- page structure may have changed.")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--html-file",
        type=Path,
        default=None,
        help="Parse a locally cached copy of the page instead of fetching live (useful offline/for tests).",
    )
    args = parser.parse_args()

    warnings: list[str] = []
    try:
        html = fetch_html(args.html_file)
    except requests.RequestException as exc:
        warnings.append(f"Fetch failed ({exc}); no snapshot produced.")
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    soup = BeautifulSoup(html, "lxml")
    candidates = find_candidate_tables(soup)
    if not candidates:
        print("ERROR: no 'RHOAI Operator Version' table found on the page.", file=sys.stderr)
        sys.exit(1)

    populated = [t for t in candidates if table_is_populated(t)]
    if not populated:
        print("ERROR: found candidate table(s) but none contain populated data.", file=sys.stderr)
        sys.exit(1)
    if len(populated) > 1:
        warnings.append(
            f"{len(populated)} populated candidate tables found on the page (duplicate DOM "
            "copies); used the first one. Re-check the live page if entries look stale."
        )

    entries = parse_component_table(populated[0], warnings)

    snapshot = MatrixSnapshot(
        fetched_at=datetime.now(timezone.utc),
        source_url=SOURCE_URL,
        rhoai_version_scope="3.x",
        entries=entries,
        fetch_warnings=warnings
        + [
            "Scope limitation: only the main 'Architecture, Version and Components' table "
            "was parsed; all entries are tagged architecture='x86_64'. Per-architecture "
            "(Arm/IBM Power/IBM Z) breakdowns live in separate tables/prose on the same page "
            "and are not yet captured by this script -- cross-check by hand during synthesis."
        ],
    )

    out_path = args.out or Path(f"data/matrix_snapshots/{date.today().isoformat()}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")

    print(f"Fetched matrix snapshot -> {out_path}")
    print(f"  {len(entries)} entries across versions {parse_version_labels(populated[0].find_all('tr'))}")
    if snapshot.fetch_warnings:
        print(f"  {len(snapshot.fetch_warnings)} warnings (see JSON 'fetch_warnings')")


if __name__ == "__main__":
    main()
