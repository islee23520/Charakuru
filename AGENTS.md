# PROJECT KNOWLEDGE BASE

**Generated:** 2026-11-07
**Commit:** 11de779
**Branch:** main

## OVERVIEW
Fork (`islee23520/Charakuru`) of the Charakuru 1.1.0 docs-and-kit repo (upstream `nanocle/Charakuru`). Since `7787224`+`11de779` this root repo keeps only the upstream shape (docs, licenses, issue templates); every fork pipeline artifact lives in the `characters/` submodule (`seoul-dengoku-gfx`). The cast's final export pipeline is Godot, not VRM (2026-11-07 decision).

## STRUCTURE
```
Charakuru/
├── characters/        # SUBMODULE seoul-dengoku-gfx: _cast data, tools, kit rules, per-person assets (own AGENTS.md)
├── docs/              # upstream user docs (en/ja/ko/zh-CN, sets incomplete) + images/
├── public/            # third-party license texts only (fonts, vendor libs)
├── .github/ISSUE_TEMPLATE/   # upstream report templates (en/ja/ko/zh-CN)
├── tools/outfit-description-check.mjs   # UNTRACKED duplicate of characters/tools/ (byte-identical)
└── outfit-description.schema.json       # UNTRACKED duplicate of characters/seoul-charakuru-kit/ (byte-identical)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Anything fork/cast/pipeline | `characters/AGENTS.md` | submodule KB: `_cast`, tools, kit, persons |
| Cast manifest, slots, credit ledger | `characters/_cast/*.json` | detail in `characters/tools/cast/AGENTS.md` |
| Quality gates G0-G5, vision review | `characters/tools/gate/` | own AGENTS.md |
| Fork authoring / review / research docs | `characters/docs/` | authoring-rules.ko.md, gate/vision-review.ko.md, research/ |
| Fork kit rules (bases, outfits, prompts) | `characters/seoul-charakuru-kit/` | entry README.ko.md; decisions in RULES.ko.md |
| Upstream user docs | `docs/` | tutorial, getting-started, browser-use, preview-user, license |

## CODE MAP
The root repo has no runnable code. In the submodule, centrality is by process spawn, not import: harnesses run CLIs as child processes; the only in-repo imports are three files under `characters/tools/gate/` (`png.mjs` <- `image-check.mjs` + `fixtures/make-arms-down.mjs`; `make-arms-down.mjs` <- `verify.mjs`). Highest fan-in: `gate.mjs` and `render-views.py` (4 referrers each), `credit-ledger.mjs` (2). Per-tool maps live in the submodule KBs.

## CONVENTIONS
- Root repo stays upstream-shaped: fork work goes into `characters/` only (`7787224` restored the upstream structure on purpose).
- No `package.json`, no npm deps anywhere: `.mjs` uses only `node:*` builtins; Python runs inside Blender 5.2, except `compose-board.py` (system Python + Pillow).
- Commit messages (both repos): Korean, conventional prefix (`feat(cast):`, `fix(gate):`, `docs(research):`), sentence ending `~다.`
- Upstream docs come as `<name>.md` + `<name>.{ja,ko,zh-CN}.md` with incomplete sets - do not "fix" them unless asked.
- Absolute Windows paths are normal (`E:/git/...`, Blender install path). Required external inputs: `E:/git/seoul-dengoku-wiki` (portraits/tokens), `E:/git/seoul-dengoku-tools` (gate fixture REF), `E:/charakuru-kit-v1.1.1` (installed kit, read-only).

## ANTI-PATTERNS (THIS PROJECT)
- Never commit fork content to the root repo, and never file fork work upstream: upstream accepts Issues only, no PRs.
- Never redistribute the kit or extract bundled assets: LICENSE v1.2 (source-available, English authoritative) forbids redistribution (ZIPs, mirrors, registries, Releases/Pages, hosted forks) without written permission; bundled assets stay in the kit; keep THIRD_PARTY_NOTICES intact.
- Never treat root `tools/` + `outfit-description.schema.json` as canonical - they are untracked duplicates; canonical copies live in the submodule (`characters/tools/`, `characters/seoul-charakuru-kit/`).
- Never spend Tripo credits without `credit-ledger.mjs check` first; every Tripo CLI call gets `--dry-run --json` before the real run.
- Never let an agent pass a gate, confirm a REVIEW_PASS, or pick a candidate on the user's behalf.

## UNIQUE STYLES
- Fork-only docs are Korean (`*.ko.md`); submodule KBs mix Korean (`characters/`, kit) and English (`tools/cast`, `tools/gate`).
- Person IDs `person-0998`...; person folders are romanized Korean names (`iilseop`, `gimyeongyu`, ...).

## COMMANDS
All runnable entry points live in the submodule. Run from `characters/`:
```bash
cd characters
node tools/gate/verify.mjs                    # gate records (no Blender)
node tools/gate/verify-path.mjs               # path traversal / junction cases
node tools/gate/vision/verify.mjs             # vision checker (real G5 render; Blender 5.2)
node tools/gate/fixtures/make-fixtures.mjs    # Blender auto-check suite, slow
node tools/cast/verify.mjs                    # manifest/ledger/contract/selection (needs seoul-dengoku-wiki)
node tools/cast/verify-stage.mjs              # ledger stage-reject cases
node seoul-charakuru-kit/checks/check-sheets.mjs _base/female   # kit sheet checker
node test_vrm_load.mjs <file.vrm> <name>      # VRM load smoke check
```

## NOTES
- `characters/` submodule is dirty by design (2026-11-07): untracked `_base/`, `seoul-charakuru-kit/`, `tools/dna-metahumans/`, `tools/outfit-description-check.mjs`, `.gitattributes`; modified `_cast/*.json`.
- `characters/workspace/` is gitignored scratch (Blender attempts, renders, VISION-LOGs, intermediate blends) - never enumerate or commit.
- The kit app itself (`npm start`, `127.0.0.1:8766`, `agent-session.mjs`) is NOT in this checkout; it ships in the release ZIP. `seoul-charakuru-kit/` is the fork's own docs+tools bundle, not that app.
- Untracked local scaffolding: `.tripo/` (Tripo CLI context), `.ruff_cache/` (ruff 0.14.14, no repo config), `.omo/` (`ulw-execute/ledger.jsonl` is the decision record cited by kit RULES.ko.md).
- Sibling worktree `E:/git/Charakuru-wt/modular-cast-w0` (branch `ulw/modular-cast-w1`) predates the restructure.
- Only `iyeon` and `jojaepyo` have full working trees and `exports/*.vrm`; the other 7 persons are cast-registered (TRAITS.md only).
