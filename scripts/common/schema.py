"""
Shared Pydantic schemas for the RHOAI Version Tracker pipeline.

These schemas are the contract between every stage of the pipeline:

    PDF  --extract_release_notes.py-->  ExtractedRelease (data/extracted/<version>.json)
    live matrix page  --fetch_support_matrix.py-->  MatrixSnapshot (data/matrix_snapshots/<date>.json)
    (ExtractedRelease + MatrixSnapshot + prior FeatureRegistry)
        --analyzed by a Cursor agent session, guided by prompts/synthesize_release.md-->
        updated FeatureRegistry (data/registry/feature_registry.json)
        + VersionDiff (data/diffs/<prev>_to_<new>.json)
    FeatureRegistry + VersionDiff --validate_registry.py--> pass/fail
    FeatureRegistry + VersionDiff --build_site.py--> docs/*.html

Keep this file dependency-free (Pydantic only) so every script can import it
without pulling in PDF/HTTP/templating libraries transitively.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class Status(str, Enum):
    """Feature/API lifecycle labels, as defined in the reference document's
    'How to read the lifecycle labels' section."""

    GA = "GA"
    TP = "TP"  # Technology Preview
    DP = "DP"  # Developer Preview
    DEPRECATED = "Deprecated"
    REMOVED = "Removed"
    CHANGE = "Change"  # non-status-changing but structurally significant change


class RiskColor(str, Enum):
    """Risk colour legend, carried directly from the reference document:

    green  = supported / GA
    amber  = preview instability, migration caution, or an unresolved
             release-notes-vs-matrix conflict
    red    = a confirmed production-impacting removal or breaking change
             to a supported GA path
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class ConfidenceBand(str, Enum):
    """Confidence bands used by the reference document's 'Notes on Method'."""

    DIRECTLY_SOURCED = "directly_sourced"
    SOURCED_BUT_INTERPRETIVE = "sourced_but_interpretive"
    INFERRED_FROM_ABSENCE = "inferred_from_absence"


class Milestone(str, Enum):
    EA1 = "EA1"
    EA2 = "EA2"
    GA = "GA"


# ---------------------------------------------------------------------------
# 1) ExtractedRelease — deterministic output of extract_release_notes.py
# ---------------------------------------------------------------------------


class TextItem(BaseModel):
    """A single bullet/paragraph item lifted from a release-notes chapter."""

    title: str
    detail: str
    milestone: Optional[Milestone] = None
    chapter: str
    issue_id: Optional[str] = None  # e.g. RHOAIENG-87834, AIPCC-18235


class IssueItem(BaseModel):
    """A resolved/known issue, which is always keyed by a tracker ID."""

    issue_id: str
    milestone: Milestone
    detail: str
    workaround: Optional[str] = None
    chapter: str = "known_issues"


class ExtractedRelease(BaseModel):
    """Structured, chapter-tagged parse of one release-notes PDF.

    Mirrors the fixed chapter skeleton every RHOAI release-notes PDF shares:
    Ch.2 New Features/Enhancements, Ch.3 Technology Preview (per milestone),
    Ch.4 Developer Preview (per milestone), Ch.5 Support Removals
    (Deprecated / Removed), Ch.6 Resolved Issues (per milestone),
    Ch.7 Known Issues (per milestone), Ch.8 Product Features.
    """

    version: str  # e.g. "3.5"
    source_pdf: str
    extracted_at: datetime
    ga_date: Optional[date] = None
    milestones_covered: list[Milestone] = Field(default_factory=list)

    new_features: list[TextItem] = Field(default_factory=list)
    enhancements: list[TextItem] = Field(default_factory=list)
    tech_preview: list[TextItem] = Field(default_factory=list)
    dev_preview: list[TextItem] = Field(default_factory=list)
    deprecated: list[TextItem] = Field(default_factory=list)
    removed: list[TextItem] = Field(default_factory=list)
    resolved_issues: list[IssueItem] = Field(default_factory=list)
    known_issues: list[IssueItem] = Field(default_factory=list)
    product_features_appendix: list[TextItem] = Field(default_factory=list)

    extraction_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2) MatrixSnapshot — deterministic output of fetch_support_matrix.py
# ---------------------------------------------------------------------------


class MatrixEntry(BaseModel):
    component: str
    component_version: Optional[str] = None
    rhoai_version: str
    architecture: str  # x86_64 | aarch64/Arm | IBM Power | IBM Z | AKS | CKS | EKS ...
    status: Status
    notes: Optional[str] = None


class MatrixSnapshot(BaseModel):
    fetched_at: datetime
    source_url: str
    rhoai_version_scope: str  # e.g. "3.x"
    entries: list[MatrixEntry] = Field(default_factory=list)
    fetch_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3) FeatureRegistry — the cumulative rollup an agent session maintains
# ---------------------------------------------------------------------------


class SourceConflict(BaseModel):
    """An explicit, unresolved disagreement between two official sources.

    Never silently resolved to one label — both claims are preserved verbatim
    per the reference document's method (Supported Configurations matrix ->
    release notes -> product guides -> press, used only to corroborate).
    """

    feature_id: str
    topic: str
    claim_a_source: str
    claim_a_value: str
    claim_b_source: str
    claim_b_value: str
    note: Optional[str] = None


class TimelineEntry(BaseModel):
    """One version's status row for a feature (Part 2 style rollup)."""

    version: str
    status: Status
    detail: str
    source: str  # e.g. "release_notes:3.5", "matrix_snapshot:2026-09-14", "MD:Part2"
    milestone: Optional[Milestone] = None
    confidence: ConfidenceBand = ConfidenceBand.DIRECTLY_SOURCED


class FeatureEntry(BaseModel):
    feature_id: str  # stable slug, e.g. "maas-core", "ogx-core", "llm-d-core"
    name: str
    category: str  # e.g. "MaaS", "OGX", "Guardrails", "MLflow", "Evaluation" ...
    current_status: Status
    risk_color: RiskColor
    timeline: list[TimelineEntry] = Field(default_factory=list)
    source_conflicts: list[SourceConflict] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)  # prior names, e.g. "Llama Stack"


class DeprecationTimelineEntry(BaseModel):
    """Flat, one-row-per-feature table mirroring reference doc Part 1."""

    feature: str
    introduced: str
    introduced_is_placeholder: bool = False
    deprecated: Optional[str] = None
    removed_status: str  # free text, e.g. "Removed at 3.0", "Not yet removed"
    notes: Optional[str] = None
    risk_color: RiskColor


class IntegrationNote(BaseModel):
    """Cross-cutting integration note (Part 3 style): does feature A actually
    connect to feature B, confirmed/inferred/absent."""

    title: str
    detail: str
    risk_color: RiskColor
    confidence: ConfidenceBand = ConfidenceBand.DIRECTLY_SOURCED


class OperationalNote(BaseModel):
    """Operational dimension note (Part 4 style): upgrade paths, hardware,
    licensing, renames, API tiers."""

    category: str  # "Upgrade Path" | "Hardware/Architecture" | "Licensing" | "Renames" | "API Tiers"
    detail: str
    risk_color: RiskColor = RiskColor.AMBER


class OgxProviderEntry(BaseModel):
    """Per-endpoint / per-provider granularity (Part 5 style), currently
    specific to OGX but structurally reusable for any similarly-shaped
    componentry table."""

    provider_api: str
    provider: str
    how_to_enable: str
    disconnected_supported: bool
    status: Status


class VersionMeta(BaseModel):
    version: str
    milestones: list[Milestone] = Field(default_factory=list)
    ga_date: Optional[date] = None
    is_eus: bool = False

    # Official Red Hat OpenShift AI Self-Managed Life Cycle overlay, sourced
    # from https://access.redhat.com/support/policy/updates/rhoai-sm/lifecycle
    # (the Life Cycle Dates table, backed by the public product-life-cycles
    # API). full_support_end / eus_end drive the dynamically computed
    # "current lifecycle phase" shown on the dashboard release timeline --
    # recomputed at every `build_site.py` run against the build date, so the
    # site never needs a manual status flip when a phase boundary passes.
    full_support_end: Optional[date] = None
    eus_end: Optional[date] = None
    openshift_versions: Optional[str] = None
    lifecycle_note: Optional[str] = None


class FeatureRegistry(BaseModel):
    """The single cumulative file the whole site is rendered from."""

    schema_version: str = "1.0"
    generated_at: datetime
    versions: list[VersionMeta] = Field(default_factory=list)
    features: list[FeatureEntry] = Field(default_factory=list)
    deprecation_timeline: list[DeprecationTimelineEntry] = Field(default_factory=list)
    integration_notes: list[IntegrationNote] = Field(default_factory=list)
    operational_notes: list[OperationalNote] = Field(default_factory=list)
    ogx_provider_table: list[OgxProviderEntry] = Field(default_factory=list)
    source_conflicts: list[SourceConflict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4) VersionDiff — scoped diff between two consecutive versions
# ---------------------------------------------------------------------------


class BreakingChange(BaseModel):
    feature_id: str
    feature_name: str
    what_changed: str
    migration_note: str
    risk_color: RiskColor = RiskColor.RED


class VersionDiff(BaseModel):
    from_version: str
    to_version: str
    generated_at: datetime

    newly_ga: list[str] = Field(default_factory=list)  # feature_ids
    newly_deprecated: list[str] = Field(default_factory=list)
    newly_removed: list[str] = Field(default_factory=list)
    new_tp: list[str] = Field(default_factory=list)
    new_dp: list[str] = Field(default_factory=list)
    breaking_changes: list[BreakingChange] = Field(default_factory=list)
    source_conflicts: list[SourceConflict] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)  # short human-readable bullets
