# Synthesize a new release into the feature registry

This is the instruction checklist a Cursor agent session follows to ingest a
new Red Hat OpenShift AI release into this tracker. There is no API call
here — you (the agent reading this) *are* the analysis step. Work through
this checklist in order, editing files directly, the same way you would in
any other agent session.

Trigger phrase for a human to start this: **"ingest `<version>`"** (e.g.
"ingest 3.6"), with the new release-notes PDF already placed at
`data/raw/<version>.pdf`.

## 0. Inputs you have available

- `data/raw/<version>.pdf` — the new release notes.
- `data/registry/feature_registry.json` — the cumulative registry (all prior versions). This is what you will update.
- `data/extracted/<prev-version>.json` — the previous release's structured extract, for continuity/context.
- `scripts/common/schema.py` — the Pydantic contract every JSON file in this repo must satisfy. Read it before editing anything.

## 1. Run the deterministic steps first

```
source .venv/bin/activate
python scripts/extract_release_notes.py data/raw/<version>.pdf
python scripts/fetch_support_matrix.py
```

This produces `data/extracted/<version>.json` (chapter-tagged items, milestone-scoped
to `<version>` only — older-version carry-over sections are already skipped)
and a fresh `data/matrix_snapshots/<date>.json` (today's live Supported
Configurations matrix).

**Spot-check both outputs before proceeding.** Read `extraction_warnings` /
`fetch_warnings` in each file. The extractor's title/body split is a
font-heuristic first pass — skim `new_features`, `tech_preview`, `deprecated`,
and `removed` for obviously mis-split or truncated items and fix them by hand
in the JSON if needed. Do not treat either file as ground truth without a
skim; treat it as a very good first draft of the primary source.

## 2. Entity resolution: link new items to registry features

For every item in `new_features`, `enhancements`, `tech_preview`, `dev_preview`,
`deprecated`, and `removed` in the new extract:

- Search `feature_registry.json`'s `features[]` for a matching `feature_id`
  by name, `category`, and prior `timeline[].detail` — not just an exact
  string match. Release notes restate the same feature in different words
  release over release (e.g. "Distributed Inference with llm-d" vs.
  "llm-d flow control" are two *different* features — the second is a
  sub-feature of the first, tracked separately, exactly as `flow-control-llmd`
  is split out from `llm-d-core` in the seed data).
- If you find a match, you will **append** a new `TimelineEntry` to that
  feature's `timeline[]` in step 4 — never overwrite or delete prior entries.
- If nothing matches, this is a genuinely new feature: propose a new
  `feature_id` (lowercase, hyphenated, stable — this slug is permanent once
  created, other pages will link to it) and a `category` consistent with the
  existing ones (`MaaS`, `OGX`, `Guardrails`, `MLOps`, `Evaluation`,
  `Agents/MCP`, `Distributed Training`, `Model Serving`, `Model Registry`,
  `Feature Store / AutoML / AutoRAG`, `Platform`, or a new category if truly
  warranted).
- Also check `deprecation_timeline[]` (the flat Part-1-style table) for
  deprecated/removed items — a feature can legitimately appear in **both**
  `features[]` (rich timeline) and `deprecation_timeline[]` (flat one-row
  summary) at once. Don't skip the flat table just because you updated the
  rich one.

## 3. Status-transition classification — and hunting for breaking changes

For each linked feature, compare:

- what `feature_registry.json` says its `current_status` was, vs.
- what the new release notes say now, vs.
- what today's `matrix_snapshots/<date>.json` says for that same component.

**Explicitly scan every promotion (TP→GA, DP→TP, etc.) for breaking-change
language before assuming it's purely additive.** Signal phrases that mean
"this needs a `breaking_changes` entry, not just a status bump":

- "renamed to", "rename"
- "API group changes from `X` to `Y`"
- "metrics prefix changes"
- "activation field moves from `spec.components.a...` to `spec.components.b...`"
- "no automated conversion path", "manual migration required"
- a CRD `kind` or API `version` changing (e.g. `v1alpha1` → `v1beta1`)

The canonical worked example already in this registry: `flow-control-llmd`
moved Technology Preview (3.4) → GA (3.5) in the *same release* that its API
group, metrics prefix, and saturation-detector config path all changed. GA
status and breaking-change status are independent axes — a feature can be
both at once. When you find one of these, add an entry to the relevant
`VersionDiff.breaking_changes[]` (see step 6) *in addition to* the normal
timeline update, and set that feature's `risk_color` to `"red"` even if its
`current_status` is `"GA"`.

## 4. Update the registry

Edit `data/registry/feature_registry.json` directly:

- Append new `TimelineEntry` rows (`version`, `status`, `detail`, `source:
  "release_notes:<version>"` or `"matrix_snapshot:<date>"`, `milestone` if
  applicable). **Never delete or edit an existing timeline row.**
- Update `current_status` and `risk_color` to reflect the new latest state.
- Add any new `feature_id` entries you identified in step 2.
- Add/append rows to `deprecation_timeline[]` for anything in the new
  extract's `deprecated`/`removed` lists.
- Bump `versions[]` with a new `VersionMeta` for `<version>` (milestones
  covered, `ga_date` if this is a GA extraction, `is_eus` if applicable).
- Update `generated_at` to now.

## 5. Conflict detection against the live matrix

For every feature you touched, check `matrix_snapshots/<date>.json` for the
same component. If the release notes and the matrix disagree on status,
architecture availability, or version — **do not silently pick one.** Add a
`SourceConflict` to both the feature's own `source_conflicts[]` and the
registry's top-level `source_conflicts[]`, citing both sources verbatim.
This registry already carries five such conflicts from the 3.5 cycle
(EvalHub GA-vs-TP, OGX component-vs-integration maturity, NeMo Guardrails on
IBM Z, KubeRay version, AutoRAG on IBM Power) as a reference for what these
look like — follow that pattern.

Source precedence when you *do* need a single answer for prose (e.g. a
dashboard headline status): **Supported Configurations matrix → release
notes → product guides → press coverage (corroboration only, never origin).**
Preserve the release-note claim as history even when the matrix overrides it
for formal support-status purposes.

## 6. Write the version diff

Create `data/diffs/<prev-version>_to_<version>.json` (schema: `VersionDiff`):

- `newly_ga`, `newly_deprecated`, `newly_removed`, `new_tp`, `new_dp`: lists
  of `feature_id`s that changed bucket this release.
- `breaking_changes[]`: every item flagged in step 3, with a `migration_note`
  that is actually actionable (what to change, not just that something changed).
- `source_conflicts[]`: copy the conflicts identified in step 5 that are new
  this release (don't re-list ones already recorded in a prior diff).
- `highlights[]`: 5-10 short, human-readable bullets for the dashboard's
  "what's new" cards — headline items only, not everything.

## 7. Validate before anything ships

```
python scripts/validate_registry.py
```

Fix anything it flags. It checks: every `TimelineEntry`/`DeprecationTimelineEntry`
has a non-empty `detail`; every `red`/`amber` entry has a real `detail` (not
a placeholder); no feature's `timeline[]` shrank versus the version-control
history (i.e. you didn't accidentally drop prior rows); no duplicate or
orphaned `feature_id`s; every `SourceConflict` cites two distinct sources.

## 8. Build the site and review

```
python scripts/build_site.py
```

This regenerates `docs/`. Open `docs/index.html` and `docs/matrix.html`
locally and skim them — this is your last check before anything is public.

## 9. Ship it

Commit `data/` and `docs/` together. Either push directly to `main` (Pages
picks it up automatically) or open a PR first if you'd rather review the
diff asynchronously — your call, there's no required gate here since you
already reviewed the output in steps 1, 7, and 8.

---

## Reference: lifecycle labels

| Label | Applies to | Meaning |
|---|---|---|
| **DP** | Feature | Unsupported by Red Hat; early implementation, may change/disappear at any time. |
| **TP** | Feature | No production SLA; evaluation/feedback only, compatibility not guaranteed. |
| **GA** | Feature/release | Production supported, on listed architectures/configs only. |
| **Deprecated** | Feature/API | Present for now; avoid new adoption, plan migration. |
| **Removed** | Feature/API | Unavailable; workloads/automation must have already moved. |

## Reference: risk color legend

- **Green** — GA / production supported, no open conflict.
- **Amber** — TP/DP preview instability, migration caution, or an unresolved
  release-notes-vs-matrix conflict.
- **Red** — a confirmed production-impacting removal, or a breaking change to
  a previously-supported GA path (including a breaking change bundled into a
  GA *promotion*, per the flow-control-llmd precedent).

## Reference: confidence bands

- **directly_sourced** — a specific chapter/table states the claim explicitly. Use this for nearly everything, since inputs are already chapter-tagged official text.
- **sourced_but_interpretive** — facts are documented but the conclusion connects multiple statements.
- **inferred_from_absence** — no confirming or contradicting statement was found after a genuine search (e.g. the Feature Store↔AutoRAG integration note). Rare — don't overuse this to paper over a search you didn't actually do.
