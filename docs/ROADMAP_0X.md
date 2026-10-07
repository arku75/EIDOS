# EIDOS 0.x Roadmap

EIDOS is being reconstructed as a **pre-1.0 capability history**. The old June 2026 `v1.0.0` label is preserved as historical publication metadata, not treated as proof that the product reached 1.0 maturity.

## Versioning rule

A retrospective version is assigned only when a dated evidence boundary can show what capability existed at that point.

No release number should be created only to make the project appear mature.

## Provisional genealogy

The current working map is intentionally marked **provisional** until every boundary has repository or machine evidence:

| Line | Working period | Working interpretation | Status |
|---|---|---|---|
| pre-0.1 | Apr–May 2026 | private genesis and early architecture | PROVISIONAL |
| 0.1 | Jun 2026 | first clean public line | PROVISIONAL |
| 0.2 | late Jun–Jul 2026 | tool/capability learning line | PROVISIONAL |
| 0.3 | Jul–Aug 2026 | autonomy, terminal and body integration | PROVISIONAL |
| 0.4 | Sep 2026 | perception, causal/effect verification and insect research line | PROVISIONAL |
| 0.5 | Oct 2026 | unified identity, Desktop 2, capability persistence/reuse and consolidation | PROVISIONAL |

These labels must not be converted into Git tags until the genealogy audit closes.

## Gate for the next public preview

A new `0.x` preview requires:

- clean-clone installation on a fresh Linux environment;
- core CI checks passing;
- canonical README/product/status agreement;
- no known exposed secrets in the release surface;
- one reproducible end-to-end acceptance story;
- clear rollback/known-limitations documentation;
- `EIDOS-OFFICIAL` no longer competing as a second canonical repository.

## Gate for 1.0

1.0 should mean a stable product contract, not a node count or a long feature list.

At minimum:

- one supported installation/deployment path;
- one canonical CLI/runtime;
- explicit authorization policy for body/tool actions;
- reproducible persistence and capability reuse;
- end-to-end effect verification;
- stable migration/backup/rollback;
- documented security boundary;
- versioned API/Bridge contract;
- release packaging and upgrade path;
- current public demo and acceptance tests.

Until those gates are met, EIDOS remains an active 0.x research/product line.
