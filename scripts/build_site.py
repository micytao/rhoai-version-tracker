#!/usr/bin/env python3
"""
build_site.py — renders data/registry/feature_registry.json (+ any
data/extracted/*.json and data/diffs/*.json) into the static site in docs/,
using the Jinja2 templates in templates/ and the shared assets in assets/.

No framework, no client-side data fetching for the main content (everything
is server-rendered at build time); a small vanilla-JS file in docs/assets/
handles client-side filtering on the Feature Matrix and Migration Checklist
pages only.

Usage:
    python scripts/build_site.py [--out docs]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.schema import ExtractedRelease, FeatureRegistry, VersionDiff  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "data" / "registry" / "feature_registry.json"
EXTRACTED_DIR = ROOT / "data" / "extracted"
DIFFS_DIR = ROOT / "data" / "diffs"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"


def version_sort_key(v: str):
    parts = re.split(r"[.\-]", v)
    key = []
    for p in parts:
        m = re.match(r"\d+", p)
        key.append(int(m.group()) if m else 0)
    return tuple(key)


def load_registry() -> dict:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    validated = FeatureRegistry.model_validate(raw)
    return json.loads(validated.model_dump_json())


def load_extracted_releases() -> dict[str, dict]:
    releases = {}
    if not EXTRACTED_DIR.exists():
        return releases
    for path in sorted(EXTRACTED_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        validated = ExtractedRelease.model_validate(raw)
        releases[validated.version] = json.loads(validated.model_dump_json())
    return releases


def load_diffs() -> list[dict]:
    diffs = []
    if not DIFFS_DIR.exists():
        return diffs
    for path in sorted(DIFFS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        validated = VersionDiff.model_validate(raw)
        diffs.append(json.loads(validated.model_dump_json()))
    return diffs


def build_matrix_data(registry: dict) -> tuple[list[str], list[dict], list[str]]:
    all_versions = set()
    for f in registry["features"]:
        for t in f["timeline"]:
            all_versions.add(t["version"])
    matrix_versions = sorted(all_versions, key=version_sort_key)

    matrix_features = []
    for f in registry["features"]:
        by_version = {}
        for t in f["timeline"]:
            by_version[t["version"]] = {
                "status": t["status"],
                "detail": t["detail"],
                "risk_color": _status_risk(t["status"]),
            }
        matrix_features.append({
            **f,
            "by_version": by_version,
            "all_statuses": sorted({t["status"] for t in f["timeline"]}),
        })
    matrix_features.sort(key=lambda f: (f["category"], f["name"]))

    categories = sorted({f["category"] for f in registry["features"]})
    return matrix_versions, matrix_features, categories


def _status_risk(status: str) -> str:
    if status == "GA":
        return "green"
    if status == "Removed":
        return "red"
    return "amber"  # TP, DP, Deprecated, Change


def build_highlights(registry: dict, latest_version: str) -> tuple[list[dict], list[dict]]:
    highlights, red_highlights = [], []
    for f in registry["features"]:
        if not f["timeline"]:
            continue
        latest_entry = f["timeline"][-1]
        if latest_entry["version"] != latest_version:
            continue
        row = {
            "feature_id": f["feature_id"],
            "name": f["name"],
            "category": f["category"],
            "status": latest_entry["status"],
            "risk_color": f["risk_color"],
            "detail": latest_entry["detail"],
        }
        highlights.append(row)
        if f["risk_color"] == "red":
            red_highlights.append(row)
    highlights.sort(key=lambda h: (h["category"], h["name"]))
    return highlights, red_highlights


def compute_stats(registry: dict) -> dict:
    stats = {"green": 0, "amber": 0, "red": 0}
    for f in registry["features"]:
        stats[f["risk_color"]] = stats.get(f["risk_color"], 0) + 1
    stats["conflicts"] = len(registry["source_conflicts"])
    stats["deprecations"] = len(registry["deprecation_timeline"])
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = load_registry()
    extracted_releases = load_extracted_releases()
    diffs = load_diffs()

    sorted_versions = sorted(registry["versions"], key=lambda v: version_sort_key(v["version"]))
    registry["versions"] = sorted_versions
    latest_version = sorted_versions[-1]["version"] if sorted_versions else None

    matrix_versions, matrix_features, categories = build_matrix_data(registry)
    highlights, red_highlights = build_highlights(registry, latest_version)
    stats = compute_stats(registry)
    version_pages = sorted(extracted_releases.keys(), key=version_sort_key)

    breaking_changes = []
    for d in diffs:
        for bc in d["breaking_changes"]:
            breaking_changes.append({**bc, "from_version": d["from_version"], "to_version": d["to_version"]})

    upgrade_notes = [n for n in registry["operational_notes"] if n["category"] == "Upgrade Path"]

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    base_ctx = {
        "generated_at": registry["generated_at"],
        "latest_version": latest_version,
        "version_pages": version_pages,
    }

    def render(template_name: str, out_path: Path, ctx: dict, *, nested: bool = False):
        full_ctx = {
            **base_ctx,
            "root_prefix": "../" if nested else "",
            "asset_prefix": "../" if nested else "",
            **ctx,
        }
        html = env.get_template(template_name).render(**full_ctx)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")

    render("index.html", out_dir / "index.html", {
        "stats": stats,
        "versions": sorted_versions,
        "highlights": highlights,
        "red_highlights": red_highlights,
    })

    render("matrix.html", out_dir / "matrix.html", {
        "matrix_versions": matrix_versions,
        "features": matrix_features,
        "categories": categories,
    })

    render("migration.html", out_dir / "migration.html", {
        "breaking_changes": breaking_changes,
        "upgrade_notes": upgrade_notes,
        "deprecation_timeline": registry["deprecation_timeline"],
    })

    render("conflicts.html", out_dir / "conflicts.html", {
        "conflicts": registry["source_conflicts"],
    })

    for version, release in extracted_releases.items():
        render("version.html", out_dir / "versions" / f"{version}.html", {"release": release}, nested=True)

    # GitHub Pages: prevent Jekyll from processing this folder
    (out_dir / ".nojekyll").touch()

    # assets
    assets_out = out_dir / "assets"
    if assets_out.exists():
        shutil.rmtree(assets_out)
    shutil.copytree(ASSETS_DIR, assets_out)

    # raw data, published for transparency / programmatic consumption
    data_out = out_dir / "data"
    data_out.mkdir(exist_ok=True)
    (data_out / "feature_registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")

    pages = ["index.html", "matrix.html", "migration.html", "conflicts.html"] + [
        f"versions/{v}.html" for v in version_pages
    ]
    print(f"Built site -> {out_dir} ({len(pages)} pages)")
    for p in pages:
        print(f"  - {p}")


if __name__ == "__main__":
    main()
