# PPT Consulting QC Error Codes

This document defines stable error codes for deck schema validation and consulting-style quality checks.

## Code families

- `QC_0xx`: content quality rules
- `QC_1xx`: schema and structure issues
- `QC_2xx`: page-mode consistency issues
- `QC_9xx`: runtime/dependency issues

## Codes

| Code | Severity | Meaning | Typical fix |
|---|---|---|---|
| `QC_001_HEADLINE_NOT_ANSWER_FIRST` | major | Slide headline is not conclusion-first | Rewrite headline as claim + direction + scope/time |
| `QC_002_MULTI_MESSAGE_HEADLINE` | major | Headline likely mixes multiple messages | Split into one core claim per slide |
| `QC_003_SOURCE_REQUIRED` | fatal | Numeric claim exists without source refs | Add `source_refs` with source/date/confidence |
| `QC_004_SO_WHAT_TOO_WEAK` | minor | `so_what` is too short or generic | Add explicit implication and decision/action |
| `QC_005_EVIDENCE_MISSING` | major | Non-title slide missing proof blocks | Add at least one evidence block |
| `QC_006_CHAPTER_SEQUENCE_INVALID` | major | `chapter_tag` sequence is out of order | Reorder slides to summary -> starting_point -> strategy_house -> initiative -> roadmap |
| `QC_007_IMPLEMENTATION_READINESS_MISSING` | major | Implementation slide is missing owner/timeline/KPI/next steps | Populate `implementation.owner/timeline/success_kpis/next_steps` |
| `QC_008_STRATEGY_HOUSE_INCOMPLETE` | major | Strategy-heavy deck lacks complete strategy house fields | Add aspiration, objectives, initiatives, enablers, and foundation |
| `QC_101_SCHEMA_INVALID` | fatal | JSON violates schema | Fix required fields/types/enums |
| `QC_102_MISSING_DECK_META` | fatal | `deck_meta` is missing or invalid | Provide full deck metadata object |
| `QC_103_SLIDE_TYPE_INVALID` | fatal | Slide item is not an object | Ensure each slide entry is structured object |
| `QC_201_PAGE_MODE_FIXED_MISSING_COUNT` | major | `fixed` mode missing `page_count` | Add `deck_meta.page_count` |
| `QC_202_PAGE_MODE_RANGE_MISSING_BOUNDS` | major | `range` mode missing min/max | Add `page_count_min` and `page_count_max` |
| `QC_203_PAGE_MODE_RANGE_INVALID_BOUNDS` | major | `page_count_min > page_count_max` | Correct bounds order |
| `QC_901_MISSING_DEPENDENCY` | fatal | `jsonschema` dependency is missing | Install `jsonschema` |

## Pass criteria

- `score_total >= 85`
- no fatal issues

## Scoring default

- fatal: -15 each
- major: -8 each
- minor: -3 each
