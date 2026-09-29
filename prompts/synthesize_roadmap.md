# Synthesize a roadmap deck into the feature registry

This is the instruction checklist an agent session follows to ingest a Red Hat
AI roadmap deck (the quarterly "What's New & What's Next" session) into this
tracker. There is no API call here — you (the agent reading this) *are* the
analysis step. Work through this checklist in order, editing files directly.

Trigger phrase for a human to start this: **"ingest roadmap `<quarter>`"**
(e.g. "ingest roadmap Q4 2026"), with the deck already placed at
`data/raw/<version>-roadmap-deck-<quarter>.pdf` (git-ignored).

## Why this is NOT the release-notes flow

Roadmap decks describe features that have **not shipped**. Do not run
`extract_release_notes.py`, do not create `data/extracted/<version>.json`, do
not append `timeline[]` rows, and do not change any `current_status`. A
feature's shipped status history changes only when a release actually ships
and `prompts/synthesize_release.md` ingests its release notes. Roadmap work
lives in a separate layer:

- `FeatureEntry.planned[]` — forward-looking targets (`RoadmapTarget`).
- `VersionMeta.ga_date_is_target` — a version whose GA date is a deck target
  (e.g. 3.6 targeted for November 2026), not a shipped fact.
- `roadmap_meta` — deck provenance + the "subject to change" disclaimer.

## 0. Inputs you have available

- The deck PDF at `data/raw/...` — extract its text (`pdftotext -layout`) and
  read every slide. The deck alternates "What is it?" (context), "What's New
  (shipped)" (already tracked — cross-check, see step 4) and "What's Next"
  (the roadmap items you are ingesting).
- `data/registry/feature_registry.json` — the cumulative registry. This is
  what you will update.
- `scripts/common/schema.py` — the Pydantic contract. Read it before editing.
- `data/extracted/<latest>.json` — the latest shipped release's extract, for
  entity resolution and baseline cross-checks.

## 1. Read the schema first

Note the roadmap-only additions: `Status.PLANNED` ("Planned" — roadmap only,
**never** inside a `timeline[]` row), `RoadmapTarget` (version,
target_status, window, detail, source, confidence, milestone, breaking,
migration_note), `FeatureEntry.planned[]`, `VersionMeta.ga_date_is_target`,
and `FeatureRegistry.roadmap_meta`.

## 2. Entity resolution (same discipline as the release-notes flow)

For every "What's Next" item in the deck, find its feature:

- Search `features[]` by name, category, aliases, and prior timeline detail —
  not exact string matching. "llm-d fast model server startup" is a
  *different* sub-feature from `llm-d-core`, exactly as `flow-control-llmd`
  is split out from it.
- Existing feature → append a `RoadmapTarget` to that feature's `planned[]`.
  Never edit `timeline[]`, `current_status`, or `risk_color`.
- Genuinely new feature → new `FeatureEntry` with `current_status: "Planned"`,
  `risk_color: "amber"`, an **empty** `timeline[]`, a stable lowercase-hyphen
  `feature_id`, and a `category`/`epic` consistent with existing rows.
- If a 3.6 target builds on a feature the deck says shipped in the *previous*
  release but which no tracked source covers (not in that release's notes or
  product docs), add the baseline `timeline[]` row **sourced to the deck**
  with `confidence: "sourced_but_interpretive"` and an explicit
  "UNCORROBORATED BASELINE" caveat in the detail (see `notebooks-2.0`,
  `workflow-navigator`, `policy-mapper`, `vllm-omni` in the seed data). If the
  deck gives no status label for it, do not invent one — keep the feature
  Planned-only and note the baseline in the planned detail (see
  `inference-time-scaling`).

## 3. Window tagging and classification

Tag every target with the deck's own cadence language in `window`:
"3.6 Fast 1", "3.6 EA1", "3.6 EA2", "3.6 GA", "Q4 2026", "1H 2027", "3.7",
"uncommitted". Rules of thumb:

- The deck's summary slide ("Roadmap Summary: <version> — <month> <year>")
  is the best source for what lands in the headline version vs. later.
- Items under a "What's Next (RHAI <version>, 1H <year>)" header that the
  summary slide does NOT list under the headline version get window
  "1H 2027" (or "3.7" where the deck footnotes say so). When the header is
  genuinely ambiguous, use "<version> / 1H <year>" and mark the target
  `confidence: "sourced_but_interpretive"`.
- Deck items marked "uncommitted" keep that word in the window and detail.
- Target status: use the deck's own label when it states one ("(GA)",
  "(TP)", "(DP)"); otherwise `PLANNED` (roadmap-only "no label yet").
- **Promotions and removals**: scan every promotion (DP→TP, TP→GA) and every
  "removal"/"rename"/"deprecate" for breaking-change language. If the target
  changes a name, API surface, CRD kind, or removes a supported path, set
  `breaking: true` and write a `migration_note` that is actually actionable
  (what to change, not that something changed). Canonical examples in the
  seed data: `fms-guardrails` (planned removal), `ai-safety-operator`
  (breaking rename), `evalhub` (LMEvalJob deprecation), `notebooks-2.0`
  (1.x→2.0 migration path), `ai-gateway-praxis` (Envoy data-plane swap).

## 4. Cross-check the deck's "What's New" claims

The deck restates the *shipped* release's features on every slide. For each
"What's New" claim that touches a tracked feature, compare it with the
registry. When they disagree, **do not silently pick one** — add a
`SourceConflict` to both the feature's own `source_conflicts[]` and the
registry's top-level `source_conflicts[]`, citing both sources verbatim
(canon: the Q3 2026 deck said multi-provider external models were GA at 3.5
while the 3.5 release notes keep TP). Source precedence:
Supported Configurations matrix → release notes → product guides → roadmap
deck (corroboration/roadmap only, never origin).

## 5. Update the registry

Edit `data/registry/feature_registry.json` directly:

- Append `planned[]` targets (never touch shipped `timeline[]` rows).
- Add new Planned-status features (empty `timeline[]`).
- Add a `VersionMeta` for the targeted version: `ga_date` set to the deck's
  target month with `ga_date_is_target: true`, milestones from the deck's
  life-cycle slide, and a `lifecycle_note` saying the date is a target.
- Refresh `roadmap_meta` (deck title, quarter, captured_at, disclaimer,
  versions_covered).
- Bump `generated_at`.

## 6. Validate, build, review

```
python scripts/validate_registry.py   # must PASS; empty-timeline warnings
                                      # are expected for Planned-only features
python scripts/build_site.py
```

Skim `docs/roadmap.html`, `docs/index.html`, `docs/matrix.html`, and
`docs/migration.html`. The roadmap page must carry the disclaimer banner;
the matrix's planned column must render dashed badges; the migration page
must list planned changes separately from shipped ones.

## 7. Ship it

Commit `data/` + `docs/` together (plus any schema/site changes) and push to
`main`, or open a PR. When the release the deck described actually ships,
ingest its release notes with `prompts/synthesize_release.md` — the
confirming rows get appended to `timeline[]` and the (now historical) targets
stay in `planned[]` as the forecast-vs-actual record.

---

## Reference: lifecycle labels (shipped) vs. roadmap targets

| Label | Lives in | Meaning |
|---|---|---|
| GA / TP / DP / Deprecated / Removed | `timeline[]` | Shipped, sourced from official release notes + matrix. |
| **Planned** | `current_status` of roadmap-only features, `planned[].target_status` | Announced in a roadmap deck, not yet shipped. Never in `timeline[]`. |
| `window` | `planned[]` | The deck's own cadence language; kept verbatim. |

## Reference: risk colors for roadmap rows

- **Amber** — every roadmap target (planned instability by definition).
- **Red** — a planned breaking change (removal/rename/API swap) customers
  must plan migration for, e.g. FMS Guardrails removal, TrustyAI rename.
- **Green** — never for a planned-only feature (nothing has shipped).
