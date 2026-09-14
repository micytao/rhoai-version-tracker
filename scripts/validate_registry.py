#!/usr/bin/env python3
"""
validate_registry.py — schema + sanity checks for data/registry/feature_registry.json
and any data/diffs/*.json files.

No LLM, no network. Pure structural validation that runs before anything
(local commit or CI) ships a registry update. Exit code 0 = pass, 1 = fail.

Checks:
  1. feature_registry.json parses against the FeatureRegistry schema.
  2. No duplicate feature_id across features[].
  3. Every TimelineEntry / DeprecationTimelineEntry has a non-empty, non-
     placeholder `detail`.
  4. Every feature/entry with risk_color in {amber, red} has a real detail
     (not just whitespace or a "TBD"-style placeholder).
  5. Every SourceConflict cites two distinct sources (claim_a_source !=
     claim_b_source) and both claim values are non-empty.
  6. No feature's timeline[] shrank relative to the version checked into git
     (git HEAD) -- i.e. every version/status pair present at HEAD is still
     present now. Skipped gracefully if not a git repo or there is no prior
     commit for this file yet.
  7. Every data/diffs/*.json file parses against VersionDiff, and every
     feature_id it references (breaking_changes, newly_*, new_tp/new_dp)
     exists in the current registry.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.schema import FeatureRegistry, VersionDiff  # noqa: E402

REGISTRY_PATH = Path("data/registry/feature_registry.json")
DIFFS_DIR = Path("data/diffs")

PLACEHOLDER_DETAILS = {"", "tbd", "todo", "n/a", "-", "..."}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def ok(self) -> bool:
        return not self.errors


def load_registry(report: Report) -> FeatureRegistry | None:
    if not REGISTRY_PATH.exists():
        report.error(f"{REGISTRY_PATH} does not exist.")
        return None
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    try:
        return FeatureRegistry.model_validate(raw)
    except ValidationError as exc:
        report.error(f"{REGISTRY_PATH} failed schema validation:\n{exc}")
        return None


def check_duplicate_feature_ids(registry: FeatureRegistry, report: Report):
    seen: dict[str, int] = {}
    for f in registry.features:
        seen[f.feature_id] = seen.get(f.feature_id, 0) + 1
    for fid, count in seen.items():
        if count > 1:
            report.error(f"Duplicate feature_id '{fid}' appears {count} times in features[].")


def is_placeholder(text: str | None) -> bool:
    return text is None or text.strip().lower() in PLACEHOLDER_DETAILS


def check_details_nonempty(registry: FeatureRegistry, report: Report):
    for f in registry.features:
        if not f.timeline:
            report.warn(f"Feature '{f.feature_id}' has an empty timeline[].")
        for t in f.timeline:
            if is_placeholder(t.detail):
                report.error(f"Feature '{f.feature_id}' version {t.version}: empty/placeholder detail.")
        if f.risk_color in ("amber", "red"):
            if not f.timeline or is_placeholder(f.timeline[-1].detail):
                report.error(
                    f"Feature '{f.feature_id}' has risk_color={f.risk_color} but no real detail "
                    "on its latest timeline entry to justify it."
                )

    for d in registry.deprecation_timeline:
        if is_placeholder(d.removed_status):
            report.error(f"Deprecation timeline entry '{d.feature}': empty/placeholder removed_status.")
        if d.risk_color in ("amber", "red") and is_placeholder(d.notes) and is_placeholder(d.removed_status):
            report.warn(f"Deprecation timeline entry '{d.feature}' has risk_color={d.risk_color} but no notes.")


def check_source_conflicts(registry: FeatureRegistry, report: Report):
    all_conflicts = list(registry.source_conflicts)
    for f in registry.features:
        all_conflicts.extend(f.source_conflicts)
    for c in all_conflicts:
        if c.claim_a_source.strip().lower() == c.claim_b_source.strip().lower():
            report.error(f"SourceConflict on '{c.feature_id}' ({c.topic}): both claims cite the same source.")
        if is_placeholder(c.claim_a_value) or is_placeholder(c.claim_b_value):
            report.error(f"SourceConflict on '{c.feature_id}' ({c.topic}): a claim value is empty.")


def check_timeline_not_shrunk(registry: FeatureRegistry, report: Report):
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{REGISTRY_PATH.as_posix()}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        report.warn("git not available -- skipping timeline-regression check.")
        return
    if result.returncode != 0:
        report.warn("No committed prior version of the registry found (first commit, or not a git repo) -- skipping timeline-regression check.")
        return

    try:
        prev = FeatureRegistry.model_validate(json.loads(result.stdout))
    except (json.JSONDecodeError, ValidationError) as exc:
        report.warn(f"Could not parse prior committed registry for comparison: {exc}")
        return

    prev_by_id = {f.feature_id: f for f in prev.features}
    for fid, prev_feature in prev_by_id.items():
        current = next((f for f in registry.features if f.feature_id == fid), None)
        if current is None:
            report.error(f"Feature '{fid}' existed at HEAD but is missing now -- timeline history would be lost.")
            continue
        prev_rows = {(t.version, t.status) for t in prev_feature.timeline}
        current_rows = {(t.version, t.status) for t in current.timeline}
        missing = prev_rows - current_rows
        if missing:
            report.error(
                f"Feature '{fid}' lost timeline row(s) present at HEAD: "
                f"{sorted(missing)}. Timeline entries must only be appended, never removed."
            )


def check_diffs(registry: FeatureRegistry, report: Report):
    if not DIFFS_DIR.exists():
        return
    known_ids = {f.feature_id for f in registry.features}
    for path in sorted(DIFFS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            diff = VersionDiff.model_validate(raw)
        except ValidationError as exc:
            report.error(f"{path} failed VersionDiff schema validation:\n{exc}")
            continue

        referenced = set(diff.newly_ga + diff.newly_deprecated + diff.newly_removed + diff.new_tp + diff.new_dp)
        referenced |= {bc.feature_id for bc in diff.breaking_changes}
        unknown = referenced - known_ids
        if unknown:
            report.error(f"{path} references unknown feature_id(s) not present in the registry: {sorted(unknown)}")

        for bc in diff.breaking_changes:
            if is_placeholder(bc.migration_note):
                report.error(f"{path}: breaking_change for '{bc.feature_id}' has no actionable migration_note.")


def main():
    report = Report()
    registry = load_registry(report)
    if registry is not None:
        check_duplicate_feature_ids(registry, report)
        check_details_nonempty(registry, report)
        check_source_conflicts(registry, report)
        check_timeline_not_shrunk(registry, report)
        check_diffs(registry, report)

    if report.warnings:
        print(f"\u26a0  {len(report.warnings)} warning(s):")
        for w in report.warnings:
            print(f"   - {w}")

    if report.errors:
        print(f"\u274c {len(report.errors)} error(s):")
        for e in report.errors:
            print(f"   - {e}")
        print("\nvalidate_registry.py: FAIL")
        sys.exit(1)

    print("\u2705 validate_registry.py: PASS"
          f" ({len(registry.features) if registry else 0} features,"
          f" {len(registry.deprecation_timeline) if registry else 0} deprecation-timeline rows)")
    sys.exit(0)


if __name__ == "__main__":
    main()
