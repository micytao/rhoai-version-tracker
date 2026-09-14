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
from datetime import date, datetime, timezone
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
                "status_class": _status_class(t["status"]),
            }
        matrix_features.append({
            **f,
            "by_version": by_version,
            "all_statuses": sorted({t["status"] for t in f["timeline"]}),
            "current_status_class": _status_class(f["current_status"]),
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


# Per-status badge color, distinct from the coarser 3-way risk_color (which
# intentionally collapses TP/DP/Deprecated/Change into "amber" for the
# aggregate risk framework). This is purely a display concern -- callers
# that need the risk framework should keep using _status_risk / risk_color.
_STATUS_CLASS = {
    "GA": "status-ga",
    "TP": "status-tp",
    "DP": "status-dp",
    "Deprecated": "status-deprecated",
    "Removed": "status-removed",
    "Change": "status-change",
}


def _status_class(status: str) -> str:
    return _STATUS_CLASS.get(status, "neutral")


def _parse_date(value) -> "date | None":
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def compute_lifecycle_phase(version_meta: dict, today: "date | None" = None) -> dict:
    """Overlays the official Red Hat OpenShift AI Self-Managed Life Cycle
    (https://access.redhat.com/support/policy/updates/rhoai-sm/lifecycle) onto
    a version. Recomputed at every build against `today`, so a version's
    displayed phase (Full Support -> Extended Update Support -> End of Life)
    advances automatically as real time passes, without needing a manual
    status flip in the registry data.
    """
    today = today or datetime.now(timezone.utc).date()
    fs_end = _parse_date(version_meta.get("full_support_end"))
    eus_end = _parse_date(version_meta.get("eus_end"))

    if fs_end is None:
        return {
            "phase": "End of Life" if version_meta["version"].startswith("1.") else "Unknown",
            "phase_class": "lifecycle-eol" if version_meta["version"].startswith("1.") else "neutral",
            "until": None,
        }
    if today <= fs_end:
        return {"phase": "Full Support", "phase_class": "lifecycle-full-support", "until": fs_end.isoformat()}
    if eus_end and today <= eus_end:
        return {"phase": "Extended Update Support", "phase_class": "lifecycle-eus", "until": eus_end.isoformat()}
    return {"phase": "End of Life", "phase_class": "lifecycle-eol", "until": (eus_end or fs_end).isoformat()}


def build_swimlane_data(registry: dict, matrix_versions: list[str]) -> list[dict]:
    """One lane per feature, run-length-encoded across `matrix_versions` so
    consecutive versions with the same (carried-forward) status render as a
    single continuous colored bar segment -- a Gantt/swimlane view of the
    same timeline data the Feature Matrix shows per-cell, meant for
    at-a-glance version-to-version comparison on the dashboard.

    Status is carried forward from the last recorded timeline entry until
    the next one (a feature with no new entry at a given version hasn't
    changed status, not disappeared); versions before a feature's first
    recorded entry render as an empty/unintroduced segment.
    """
    version_index = {v: i for i, v in enumerate(matrix_versions)}
    lanes = []
    for f in registry["features"]:
        entries_by_index = {}
        for t in f["timeline"]:
            idx = version_index.get(t["version"])
            if idx is not None:
                entries_by_index[idx] = t["status"]
        if not entries_by_index:
            continue
        first_idx = min(entries_by_index.keys())

        effective = []
        current_status = None
        for i in range(len(matrix_versions)):
            if i in entries_by_index:
                current_status = entries_by_index[i]
            effective.append(current_status if i >= first_idx else None)

        segments = []
        i = 0
        while i < len(effective):
            status = effective[i]
            j = i
            while j + 1 < len(effective) and effective[j + 1] == status:
                j += 1
            segments.append({
                "status": status,
                "status_class": _status_class(status) if status else "empty",
                "span": j - i + 1,
                "start_version": matrix_versions[i],
                "end_version": matrix_versions[j],
            })
            i = j + 1

        lanes.append({
            "feature_id": f["feature_id"],
            "name": f["name"],
            "category": f["category"],
            "all_statuses": sorted({t["status"] for t in f["timeline"]}),
            "segments": segments,
        })
    lanes.sort(key=lambda l: (l["category"], l["name"]))
    return lanes


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
            "status_class": _status_class(latest_entry["status"]),
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
    build_today = datetime.now(timezone.utc).date()
    for v in sorted_versions:
        v["lifecycle"] = compute_lifecycle_phase(v, build_today)
    registry["versions"] = sorted_versions
    latest_version = sorted_versions[-1]["version"] if sorted_versions else None

    matrix_versions, matrix_features, categories = build_matrix_data(registry)
    swimlane_lanes = build_swimlane_data(registry, matrix_versions)
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
        "swimlane_versions": matrix_versions,
        "swimlane_lanes": swimlane_lanes,
        "swimlane_categories": categories,
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
