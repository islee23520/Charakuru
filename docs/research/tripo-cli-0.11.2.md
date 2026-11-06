# Tripo CLI 0.11.2 contract probe

Probed the installed CLI on 2026-11-06. `tripo update` reported already up to date at 0.11.2; `tripo --version` also printed 0.11.2. The update did not change the version. `tripo doctor --json` succeeded with Node v24.13.1, a configured API key, reachable API, and 14,140 credits / 0 frozen. `tripo balance --json` was 14,140 / 0 before and after all probes, so no credits were spent.

Raw, unsummarized stdout from `tripo docs --topic commands/generate` is saved in [task-1-docs-generate.txt](../../.omo/evidence/modular-cast-tripo-parts/task-1-docs-generate.txt), and stdout from `tripo docs --topic commands/process` is saved in [task-1-docs-process.txt](../../.omo/evidence/modular-cast-tripo-parts/task-1-docs-process.txt). The relevant accepted parameters, warnings, and prices are discussed below; complete dry-run JSON and command outputs are in `.omo/evidence/modular-cast-tripo-parts/task-1-cli-probe.json`.

## Generation

The standalone `tripo generate multiview-to-model` command does not accept `--dry-run` in 0.11.2. The CLI's documented equivalent is `tripo make ... --dry-run --json`. Four filename-hinted views were recognized as `front`, `left`, `back`, and `right`; the accepted command normalized `tripo-p2` to wire model `P2-20260801` and produced `valid: true`.

The P2 quad limits are 48–25,000 faces; triangle mode allows 48–50,000. The accepted quad request included `face_limit=6000`, `texture=false`, `pbr=false`, and `quad=true`. Warnings state that `texture=false` implies `pbr=false` and that quad output forces FBX because GLB cannot store quad topology. The dry-run estimated 100 credits for the bare-geometry multiview request, with texture credits omitted. The docs list P2 at 100–130 credits depending on texture tier.

The failure case `face_limit=30000` with `quad=true` returned `valid: false`, exit code 2, and the expected error: P2 face limit must be within 48–25,000 with quad enabled. It did not submit a task.

## Model processing

`tripo model texture <file> --texture-quality detailed --dry-run --json` is supported and returned `valid: true`. It plans model `v3.0-20250812`, `pbr=false`, and a pending local upload. Its credit estimate is `null`; the output explicitly says this does not mean free. Bundled documentation lists detailed texture at 20 credits per task, checked 2026-11-02, and notes that the current regional price should be confirmed before a real task.

`tripo mesh smartsegment` has no `--dry-run` option. Its docs accept file/URL input, `--seg-type image|model`, `--granularity coarse|medium|fine`, and `--hint`; model mode is for existing GLB input, and identity transform is auto-filled. The list price is 55 credits for model segmentation (85 for image). A `tripo make <file> --then smartsegment --dry-run --json` attempt is not equivalent: it returns `valid: false`, because smartsegment is restricted to direct file/URL input and cannot follow another task in a chain. No smartsegment submit was made.

The docs use the operation name `tripo mesh uv`; during retopology the corresponding flag is `tripo mesh decimate --model v3.0 --smart-uv`. `smart_uv` is v3.0-only and has no extra cost. Standalone `mesh uv` is documented at 20 credits, accepts `--no-bake` and `--export-orientation`, and is limited to 80,000 equivalent triangles.

## Command and safety results

- `tripo update`: already current, 0.11.2.
- `tripo --version`: 0.11.2.
- `tripo doctor --json`: all critical checks passed.
- `tripo balance --json`: 14,140 before and after.
- `tripo docs --topic commands/generate` and `tripo docs --topic commands/process`: completed; pertinent contract details above.
- Generation equivalent dry-run: `valid: true`, model `P2-20260801`.
- Texture dry-run: `valid: true`.
- Smartsegment: no dry-run support in this installed CLI; no task submitted.
- Invalid P2 quad face-limit dry-run: exit 2, correct validation error.

No paid Tripo task was submitted. Evidence, including exact JSON payloads, warnings, errors, and balance snapshots: `.omo/evidence/modular-cast-tripo-parts/task-1-cli-probe.json`.
