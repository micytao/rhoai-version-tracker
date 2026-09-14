#!/usr/bin/env python3
"""
extract_release_notes.py — deterministic PDF -> structured JSON extraction.

Parses a Red Hat OpenShift AI release-notes PDF into the ExtractedRelease
schema (scripts/common/schema.py), using the chapter/section skeleton that
has held steady release over release:

    CHAPTER 2. NEW FEATURES AND ENHANCEMENTS      -> new_features / enhancements
    CHAPTER 3. TECHNOLOGY PREVIEW FEATURES         -> tech_preview (per milestone)
    CHAPTER 4. DEVELOPER PREVIEW FEATURES          -> dev_preview (per milestone)
    CHAPTER 5. SUPPORT REMOVALS
        5.1. DEPRECATED                            -> deprecated
        5.2. REMOVED FUNCTIONALITY                 -> removed
    CHAPTER 6. RESOLVED ISSUES                     -> resolved_issues (per milestone)
    CHAPTER 7. KNOWN ISSUES                        -> known_issues (per milestone)
    CHAPTER 8. PRODUCT FEATURES                    -> product_features_appendix

No LLM is used here. Item boundaries are detected purely from PDF font
metadata (title lines are rendered in a "Medium"/"Bold" weight font at body
text size; body/detail paragraphs are rendered in the "Regular" weight family)
plus a handful of stable regexes for headings and known-issue tracker IDs
(e.g. RHOAIENG-87834, AIPCC-18235).

This is a *first pass*, not a final source of truth: font-based heuristics can
misfire on unusual layouts (tables, multi-column callouts). Every run reports
`extraction_warnings` and the output is meant to be spot-checked by whoever
runs the ingestion session before it feeds the registry.

Usage:
    python scripts/extract_release_notes.py <path-to-pdf> [--out data/extracted/<version>.json]

If --out is omitted, the output path is derived from the detected version.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.schema import (  # noqa: E402
    ExtractedRelease,
    IssueItem,
    Milestone,
    TextItem,
)

# ---------------------------------------------------------------------------
# Regexes for stable document structure
# ---------------------------------------------------------------------------

CHAPTER_RE = re.compile(r"^CHAPTER\s+(\d+)\.\s*(.+)$", re.IGNORECASE)
NUMBERED_SECTION_RE = re.compile(r"^(\d+)\.(\d+)\.\s*(.+)$")
MILESTONE_MARKER_RE = re.compile(
    r"^\d+\.\d+\s+(GA|EA1|EA2)\s+(new features|enhancements)$", re.IGNORECASE
)
VERSION_MILESTONE_IN_TITLE_RE = re.compile(r"(\d+\.\d+)\s*(GA|EA1|EA2)\b", re.IGNORECASE)
ISSUE_ID_RE = re.compile(r"\b([A-Z]{2,10}-\d{3,7})\b")
GA_DATE_RE = re.compile(r"Last Updated:\s*([\d-]+)", re.IGNORECASE)

HEADER_FOOTER_TOP_MARGIN = 20.0  # points; running header lives above this
FOOTER_BOTTOM_MARGIN = 30.0  # points; footer lives within this of page bottom
HEADING_SIZE_THRESHOLD = 13.0  # pt; chapter/section headings are >=14pt


def is_heading_weight(fontname: str) -> bool:
    return "Medium" in fontname or "Bold" in fontname


# ---------------------------------------------------------------------------
# Step 1: turn each page into a sequence of classified lines
# ---------------------------------------------------------------------------


class Line:
    __slots__ = ("text", "max_size", "medium_frac", "kind")

    def __init__(self, text: str, max_size: float, medium_frac: float):
        self.text = text
        self.max_size = max_size
        self.medium_frac = medium_frac
        self.kind = None  # filled in by classify_line()


def page_lines(page) -> list[Line]:
    words = page.extract_words(extra_attrs=["fontname", "size"], use_text_flow=False)
    if not words:
        return []
    page_height = page.height

    # group words into visual lines by rounded 'top'
    groups: dict[float, list[dict]] = {}
    for w in words:
        top = w["top"]
        if top < HEADER_FOOTER_TOP_MARGIN or top > page_height - FOOTER_BOTTOM_MARGIN:
            continue  # running header / footer
        key = round(top)
        groups.setdefault(key, []).append(w)

    lines: list[Line] = []
    for top in sorted(groups.keys()):
        ws = sorted(groups[top], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in ws).strip()
        if not text:
            continue
        max_size = max(w["size"] for w in ws)
        medium_frac = sum(1 for w in ws if is_heading_weight(w["fontname"])) / len(ws)
        lines.append(Line(text=text, max_size=max_size, medium_frac=medium_frac))
    return lines


CODE_LINE_RE = re.compile(
    r"^(oc |kubectl |curl |pip install|helm |\$\s|registry\.redhat\.io|Table \d+\.\d+\.)"
)


def classify_line(line: Line) -> str:
    """Returns one of: chapter_heading, section_heading, milestone_marker,
    title, body."""
    if CHAPTER_RE.match(line.text):
        return "chapter_heading"
    if NUMBERED_SECTION_RE.match(line.text):
        return "section_heading" if line.max_size >= HEADING_SIZE_THRESHOLD else "title"
    if MILESTONE_MARKER_RE.match(line.text):
        return "milestone_marker"
    if CODE_LINE_RE.match(line.text.strip()):
        # CLI commands / image refs / table captions render bold too, but are
        # never a real item title -- always fold back into the body.
        return "body"
    if line.medium_frac >= 0.5:
        return "title" if line.max_size < HEADING_SIZE_THRESHOLD else "section_heading"
    return "body"


# ---------------------------------------------------------------------------
# Step 2: walk the classified line stream, dispatching items into buckets
# ---------------------------------------------------------------------------


class ExtractionState:
    def __init__(self, target_version: str):
        self.target_version = target_version
        self.current_chapter_num: int | None = None
        self.current_chapter_title: str = ""
        self.current_section_title: str = ""
        self.current_section_version: str | None = None
        self.current_section_milestone: str | None = None
        self.warnings: list[str] = []

        self.new_features: list[TextItem] = []
        self.enhancements: list[TextItem] = []
        self.tech_preview: list[TextItem] = []
        self.dev_preview: list[TextItem] = []
        self.deprecated: list[TextItem] = []
        self.removed: list[TextItem] = []
        self.resolved_issues: list[IssueItem] = []
        self.known_issues: list[IssueItem] = []
        self.product_features_appendix: list[TextItem] = []

        # accumulator for the in-progress item
        self._title_parts: list[str] = []
        self._body_parts: list[str] = []

    # -- version/milestone bookkeeping -------------------------------------------------

    def enter_chapter(self, num: int, title: str):
        self.flush_item()
        self.current_chapter_num = num
        self.current_chapter_title = title
        self.current_section_title = ""
        self.current_section_version = None
        self.current_section_milestone = None

    def enter_section(self, title: str):
        self.flush_item()
        self.current_section_title = title
        m = VERSION_MILESTONE_IN_TITLE_RE.search(title)
        if m:
            self.current_section_version = m.group(1)
            self.current_section_milestone = m.group(2).upper()
        else:
            # Chapter 5 (Support Removals) has no per-milestone versioning
            self.current_section_version = self.target_version
            self.current_section_milestone = None

    def enter_milestone_marker(self, title: str):
        self.flush_item()
        m = VERSION_MILESTONE_IN_TITLE_RE.search(title)
        if m:
            self.current_section_version = m.group(1)
            self.current_section_milestone = m.group(2).upper()

    # -- item accumulation -------------------------------------------------------------

    def start_title(self, text: str):
        self.flush_item()
        self._title_parts = [text]

    def continue_title(self, text: str):
        self._title_parts.append(text)

    def add_body(self, text: str):
        self._body_parts.append(text)

    def body_ends_sentence(self) -> bool:
        """True if the current item's body text so far ends at a genuine
        sentence/paragraph boundary. Used to tell a real new item title
        apart from a short inline bold/code fragment (e.g. `kv-sqlite`,
        `v1alpha1`) that happens to render in the same title-weight font
        in the middle of a body paragraph."""
        if not self._body_parts:
            return True
        return self._body_parts[-1].rstrip().endswith((".", "!", "?", ":"))

    def flush_item(self):
        if not self._title_parts:
            self._body_parts = []
            return
        title = " ".join(self._title_parts).strip()
        detail = " ".join(self._body_parts).strip()
        self._title_parts = []
        self._body_parts = []
        self._dispatch(title, detail)

    def _dispatch(self, title: str, detail: str):
        chapter = self.current_chapter_num
        section = self.current_section_title.lower()
        version = self.current_section_version
        milestone_str = self.current_section_milestone

        # Chapters 2-4: only keep items belonging to the *current* release
        # file's own version. Older-version carry-over subsections (e.g. a
        # "3.4 GA Technology Preview Features" section retained for context
        # inside the 3.5 PDF) are intentionally skipped here -- they were
        # already captured when that earlier version's own PDF was ingested.
        in_scope = version == self.target_version

        if chapter == 2:
            bucket = self.new_features if "new features" in section or not section else self.enhancements
            if "enhancement" in section:
                bucket = self.enhancements
            if in_scope or version is None:
                bucket.append(
                    TextItem(
                        title=title,
                        detail=detail,
                        milestone=self._milestone_enum(milestone_str),
                        chapter=f"2:{self.current_section_title or 'new_features'}",
                    )
                )
            else:
                self.warnings.append(f"Skipped out-of-scope ch2 item ({version}): {title[:60]}")

        elif chapter == 3:
            if in_scope:
                self.tech_preview.append(
                    TextItem(
                        title=title,
                        detail=detail,
                        milestone=self._milestone_enum(milestone_str),
                        chapter=f"3:{self.current_section_title}",
                    )
                )
            else:
                self.warnings.append(f"Skipped out-of-scope ch3 item ({version}): {title[:60]}")

        elif chapter == 4:
            if in_scope:
                self.dev_preview.append(
                    TextItem(
                        title=title,
                        detail=detail,
                        milestone=self._milestone_enum(milestone_str),
                        chapter=f"4:{self.current_section_title}",
                    )
                )
            else:
                self.warnings.append(f"Skipped out-of-scope ch4 item ({version}): {title[:60]}")

        elif chapter == 5:
            item = TextItem(title=title, detail=detail, chapter=f"5:{self.current_section_title}")
            if "removed" in section:
                self.removed.append(item)
            else:
                self.deprecated.append(item)

        elif chapter == 6:
            self._dispatch_issue(title, detail, self.resolved_issues, "resolved_issues")

        elif chapter == 7:
            self._dispatch_issue(title, detail, self.known_issues, "known_issues")

        elif chapter == 8:
            self.product_features_appendix.append(
                TextItem(title=title, detail=detail, chapter="8:product_features")
            )
        else:
            self.warnings.append(f"Item outside chapters 2-8 ignored: {title[:60]}")

    def _dispatch_issue(self, title: str, detail: str, bucket: list, chapter_name: str):
        combined = f"{title} {detail}".strip()
        m = ISSUE_ID_RE.search(title) or ISSUE_ID_RE.search(detail)
        if not m:
            # Each known/resolved issue in the PDF is itself split into
            # bold sub-blocks ("Workaround", "NOTE", or a stray inline-code
            # fragment). Without its own tracker ID, this is a continuation
            # of the previously emitted issue, not a new one -- merge back
            # in rather than emitting a bogus UNKNOWN-0 entry.
            if bucket:
                prev = bucket[-1]
                lead = title.strip().rstrip(":").lower()
                if lead.startswith("workaround"):
                    addition = detail.strip() or title.strip()
                    prev.workaround = (prev.workaround + " " if prev.workaround else "") + addition
                else:
                    extra = f"{title} {detail}".strip()
                    prev.detail = f"{prev.detail} {extra}".strip()
            else:
                self.warnings.append(f"No issue ID + no previous issue to merge into ({chapter_name}): {title[:60]}")
            return
        issue_id = m.group(1)
        milestone = self._milestone_enum(self.current_section_milestone) or Milestone.GA
        origin_version = self.current_section_version or self.target_version
        workaround = None
        wa_match = re.search(r"Workaround:\s*(.+)$", combined)
        if wa_match:
            workaround = wa_match.group(1).strip()
        bucket.append(
            IssueItem(
                issue_id=issue_id,
                milestone=milestone,
                detail=combined if not title else f"{title} — {detail}" if detail else title,
                workaround=workaround,
                chapter=f"{chapter_name}:{origin_version} {milestone.value}",
            )
        )

    @staticmethod
    def _milestone_enum(value: str | None) -> Milestone | None:
        if not value:
            return None
        try:
            return Milestone(value.upper())
        except ValueError:
            return None


def extract(pdf_path: Path, target_version: str) -> ExtractionState:
    state = ExtractionState(target_version=target_version)
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in page_lines(page):
                kind = classify_line(line)
                if kind == "chapter_heading":
                    m = CHAPTER_RE.match(line.text)
                    state.enter_chapter(int(m.group(1)), m.group(2).strip())
                elif kind == "section_heading":
                    m = NUMBERED_SECTION_RE.match(line.text)
                    title = m.group(3).strip() if m else line.text
                    state.enter_section(title)
                elif kind == "milestone_marker":
                    state.enter_milestone_marker(line.text)
                elif kind == "title":
                    if not state._title_parts:
                        # nothing accumulated yet in this section -> genuine new title
                        state.start_title(line.text)
                    elif not state._body_parts:
                        # still-wrapping title (font-classified as its own
                        # line, immediately follows the previous title line)
                        state.continue_title(line.text)
                    elif state.body_ends_sentence():
                        # a real paragraph boundary -> genuine new item title
                        state.start_title(line.text)
                    else:
                        # short inline bold/code fragment mid-sentence
                        # (e.g. `kv-sqlite`, `v1alpha1`) -- fold back into body
                        state.add_body(line.text)
                else:  # body
                    if not state._title_parts:
                        # body text before any title in this section (rare;
                        # usually section intro sentences) -- ignore safely
                        continue
                    state.add_body(line.text)
        state.flush_item()
    return state


def detect_version_and_date(pdf_path: Path) -> tuple[str, str | None]:
    with pdfplumber.open(pdf_path) as pdf:
        first_page_text = pdf.pages[0].extract_text() or ""
    m = re.search(r"Self-Managed\s+(\d+\.\d+)", first_page_text)
    version = m.group(1) if m else "unknown"
    date_m = GA_DATE_RE.search(first_page_text)
    ga_date = date_m.group(1) if date_m else None
    return version, ga_date


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--version", type=str, default=None, help="Override detected version")
    args = parser.parse_args()

    version, ga_date = detect_version_and_date(args.pdf_path)
    if args.version:
        version = args.version

    state = extract(args.pdf_path, target_version=version)

    milestones_covered = sorted(
        {
            item.milestone
            for item in (state.new_features + state.enhancements + state.tech_preview + state.dev_preview)
            if item.milestone
        },
        key=lambda m: ["EA1", "EA2", "GA"].index(m.value),
    )

    release = ExtractedRelease(
        version=version,
        source_pdf=str(args.pdf_path),
        extracted_at=datetime.now(timezone.utc),
        ga_date=ga_date,
        milestones_covered=milestones_covered,
        new_features=state.new_features,
        enhancements=state.enhancements,
        tech_preview=state.tech_preview,
        dev_preview=state.dev_preview,
        deprecated=state.deprecated,
        removed=state.removed,
        resolved_issues=state.resolved_issues,
        known_issues=state.known_issues,
        product_features_appendix=state.product_features_appendix,
        extraction_warnings=state.warnings,
    )

    out_path = args.out or Path(f"data/extracted/{version}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(release.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")

    print(f"Extracted RHOAI {version} -> {out_path}")
    print(
        f"  new_features={len(release.new_features)} enhancements={len(release.enhancements)} "
        f"tech_preview={len(release.tech_preview)} dev_preview={len(release.dev_preview)} "
        f"deprecated={len(release.deprecated)} removed={len(release.removed)} "
        f"resolved_issues={len(release.resolved_issues)} known_issues={len(release.known_issues)} "
        f"product_features={len(release.product_features_appendix)}"
    )
    if release.extraction_warnings:
        print(f"  {len(release.extraction_warnings)} extraction warnings (see JSON 'extraction_warnings')")


if __name__ == "__main__":
    main()
