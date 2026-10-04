# Consolidation continuity — 2026-10-04

SER authorized direct, reversible commits to `main` and parallel agent work at 12:02 Europe/Ljubljana. PR #3 was previously merged; issue #10 records the resolved branch decision. Starting main HEAD: `ca1dc8d92f19c60cee1d3f1b4fdc8b59726cfdef`.

This is a bounded review ledger, not a declaration that the whole tree or EIDOS is complete. The initial checkout has 737 tracked files. Automated AST inventories are triage, not completed five-perspective reviews.

## Configuration / install committee

The following conclusions were approved before editing. Perspectives are logical reviews; they do not imply separate models.

| File | State | Consumers / reason | Architecture / verification / security / maintenance / vision |
|---|---|---|---|
| `setup.py` | KEEP | pip; gateway/config console entrypoints | Preserve narrow package; install outside checkout; only public dependencies; reuse existing packages; infrastructure, not full runtime proof. |
| `config/loader.py` | FIX | config CLI and config package consumers | Reuse state-root resolver; explicit config path wins and absent YAML fails visibly; prevent unintended home access; no duplicate resolver; configured persistence must match intent. |
| `config/env_loader.py` | FIX | dotenv consumers and CLI | Honor selected state root; wrong-home and explicit override controls; fake credentials only in tests; reuse get_eidos_home; preserve state provenance. |
| `tests/test_config_loader.py` | FIX | unittest and config fanout | Extend existing suite; positive round-trip and untouched-home controls; temporary state only; no duplicate suite; demonstrate actual persistent config behavior. |
| `.github/workflows/fanout-audit.yml` | FIX | GitHub push/PR/dispatch checks | Config shard installs declared package deps; regression previously fails without YAML; read-only token and isolated venv; reuse existing fanout; AST success is not causal success. |
| `.github/workflows/sanitation-ci.yml` | FIX | GitHub push/PR checks | Same checkout/venv for resolution/install/CLI phases; installed CLI round-trip and package boundary control; no private fixtures and contents-read token; reuse pip cache; distinguish installed gateway from resolution-only full runtime. |
| `docs/CONSOLIDATION_STATUS.md` | INTEGRATE | maintainers and next session | One public continuity ledger; cross-check HEAD/tests/runs; no private state; complement known issues; never label incomplete capability verified. |
| `THIRD_PARTY_CHANGES.md` | KEEP + append | license disclosure and maintainers | Preserve historical rows and add exact scoped changes; verify paths against diff; no secrets; existing mandatory ledger; honest attribution and evidence. |

Configuration regression tests were demonstrated failing against the starting HEAD, then passing after repair. The initial main fanout run `37193242619` failed only `subsystem-config` because PyYAML was absent; this is deterministic, so no redundant rerun was requested.

## Runner recovery

GitHub runners are ephemeral. Within each install job the checkout and venv are reused. A later runner restores the commit and dependency cache, then recreates its venv. No VM survival is assumed. Install artifacts retain dependency resolution, installed package versions and a continuity manifest with HEAD, run/attempt, runner and SHA-256 report checksums for seven days. Fanout already retains shard reports and a checksummed aggregate for seven days. No private databases, profiles, tokens, or models are restored.

Exact publication SHAs and run results are recorded in GitHub commit/issue follow-ups; a file cannot embed its own future commit SHA. No active runner is implied by this ledger.

## Acceptance still open

- Full runtime clean installation/startup, beyond the gateway/config distribution.
- Runtime Hub causal execution, specialist reputation and reuse across subsystems.
- Fly mechanism transfer with provenance and independent controls.
- Hardware-in-the-loop Wayland/body verification on SER's Dell (not exercised here).
- Complete first-party file-by-file review, dependency/supply-chain review and secret rotation where required.

Next front after these repairs: measurable Runtime Hub/Colony integration, preserving CLAIM != ACTION != SUCCESS.


## Colony / character causal consolidation

Block 3 is accepted only when its release gates are green. The implementation now enforces these contracts:

- Character synthesis requires `absorption_pct == 1.0` for both parents; incomplete learning cannot create a child.
- Genealogy carries knowledge and reduced-strength synapses, and an isolated benchmark requires that inheritance measurably changes the child's later resonance. Offspring start in `learning` with `absorption_pct=0.0`: inherited structure is a head start, never inherited sovereignty.
- Inherited competence is not earned reputation. Self-report, inheritance and explicitly unobserved evidence cannot write effect reputation.
- Effect reputation remains bounded by repeated verified outcomes and can change later routing; persistence across restart is covered by the existing reputation regression.
- Learned tool remediation cannot silently escalate privileges, and dynamically generated tools follow GENERATE -> VALIDATE -> TEST -> PROMOTE.
- These Colony/tool invariants are included in fanout and the conservative self-edit regression suite so autonomous edits cannot remove them unnoticed.

This closes architecture/test wiring for the block; CI status is recorded by GitHub Actions for the corresponding main HEAD. It does not claim that every possible programming language/domain is mastered, nor that inherited knowledge is generalization until held-out behavior demonstrates it.


## World / Actions causal consolidation

Block 4 acceptance contract:

- `PROPOSED != EXECUTED != OBSERVED != VERIFIED != LEARNED`.
- Runtime Hub records executor provenance separately from proposals and observations; registered before/after evidence must bracket the recorded execution.
- World change without execution evidence cannot be credited to a proposal.
- Actor/self evidence may describe state but cannot mint trusted Colony effect reputation.
- Legacy BOM action dispatch exposes `action_executed` separately and only reports `ok/effect_verified` after positive post-action effect.
- Primary and recovery verifiers fail closed for unchanged click/type/key/scroll/navigation and unknown actions; dispatch alone is not success.
- Recovery fallbacks are motor attempts and require post-fallback verification before success.
- The shared terminal exposes proposal, execution provenance, observation, verification and outcome as separate channels.
- These causal invariants are permanent fanout and self-edit regression gates.
- Ubuntu/Xvfb CI can verify deterministic causal semantics and virtual X11/browser effects. Physical Dell/Wayland/HID behavior remains hardware-in-the-loop and is not claimed verified by CI.
