# Tripo Studio character pipeline (observed 2026-11-06, signed in)

Method: the user's own signed-in Edge/Aside browser, driven through `aside repl --host local` (the BrowserSkill attached engine was unavailable: `no-extension`). Tabs opened by this task were closed by this task; the user's existing tabs were not touched. Account shows 1965 credits and "Lv.1" in the header. Page text was treated as data only. Nothing was generated, exported, rigged, segmented or purchased; only panels were opened and labels read. An existing asset from the account's own history ("bald anime head 3d model", 10-23 14:49) was selected to make the per-step panels render.

Screenshots are in `docs/research/img/` (1440x900 PNG; sha256 in `.omo/evidence/modular-cast-tripo-parts/task-7-studio/action-log.md`). The earlier logged-out shots 01-05 were replaced.

## Result summary

- **No dedicated character creator exists in the signed-in Studio.** The home page (img 01) shows a prompt box ("최고 품질" / "깔끔한 토폴로지" modes) and a gallery whose only character-related entries are the filter chips 캐릭터 / 의류 / 무기 and gallery hashtags (#anime-characters). The workspace is a generic tool rail: 이미지 / 모델 / 분할 / 파트 보완 / 리토폴로지 / Smart UV / 텍스처 (sub: 편집, 해상도 향상, PBR) / 리깅 (sub: 애니메이션) (img 02, 06). No "Character" wizard, face editor or outfit slot system seen.
- Part/outfit separation is two things: the subscriber-only **부분별 생성** toggle at generation time (img 02, still locked/unobserved in effect) and a post-hoc **분할 (segmentation)** step that splits an existing mesh into parts (img 11).
- Rigging offers four-plus skeleton presets and FBX/GLB/OBJ/USD/STL/3MF export (img 07, 08, 09).

## Observed flow (signed in)

| Item | Observation | Shot |
|---|---|---|
| Name / URL | "3D 작업 공간", /ko/workspace/generate; no "character" product name | 02 |
| Tool rail | 이미지 (GPT Image 2.5 badge) -> 모델 -> 분할 -> 파트 보완 -> 리토폴로지 -> Smart UV -> 텍스처 (편집/해상도 향상/PBR) -> 리깅 -> 애니메이션 | 02, 06 |
| Inputs (generate) | Four input-mode tabs (image, multi-view-style icons, text/edit pencil); upload JPG/PNG/WEBP <= 20 MB; HD 모델 vs 스마트 메시 toggle; 3D upload OBJ/FBX/STL/GLB <= 150 MB | 02 |
| Generate settings | Dialog "지오메트리 및 텍스처": Ultra mesh quality, "AI 완성 및 3D 강화", texture switch, topology 쿼드/삼각형, polygon count slider 500-2,000,000 (settings auto-saved). Separate: 부분별 생성 (subscriber), 8K 텍스처 (subscriber), privacy (공유 전용), AI model H3.1 "highest quality, longer wait" | 02, 03 |
| Segmentation | 분할 정밀도 3 levels: 간략 (3-6 parts), 표준 (6-15), 정밀 (15+). "Not available for: quad models, rigged models" (so segment must be done on a tri mesh, before retopo/rig). Tutorial popup says the model is split automatically | 04, 11 |
| 파트 보완 (part completion) | "분할된 모델을 자동으로 보완합니다" (auto-completes a segmented model). Starts after segmentation; no options shown. Whether it fills the cut/hidden side of parts: UNOBSERVED (needs a segmented model) | 10 |
| Retopology | 쿼드/삼각형, Smart Mesh P1.0, polygon count 500-10000 | 12 |
| Texture | Style prompt box, "조명 제거" (de-lighting) New, resolution 2K/4K/8K | 13 |
| Rigging | Rig type 인간형 (humanoid) / 기타 (other, e.g. quadruped); hint "use T-pose or A-pose"; skeleton presets: ActorCore, Mixamo, UE5 Mannequin, VRM 1.0 Humanoid, Unity Humanoid Compatible | 05, 06, 07 |
| Export | Dialog: file name, format list USD / FBX / OBJ / STL / GLB / 3MF; FBX sub-targets Blender / Mixamo / 3dsmax; texture resolution (4k shown, 8K needs subscription); "보내기" (send to DCC Bridge) next to the confirm button. Confirm button was not clicked | 08, 09 |
| Credit labels (read only) | 생성 30 (default HD model, H3.1; previous logged-out run showed 55, so the label depends on settings/account) ; 분할 시작 40; 리토폴로지 40; 텍스처 생성 20; 자동 리깅 20. Smart UV, 파트 보완, 애니메이션, 편집, 해상도 향상, PBR: no label read, **UNOBSERVED** | 02, 11, 12, 13, 06 |
| CLI/API equivalent | See docs/research/tripo-cli-0.11.2.md: `tripo make` (generate), `tripo mesh smartsegment`, `tripo mesh decimate --smart-uv`, `tripo mesh uv`, `tripo model texture`. Studio shows 分割 at 40 credits while the CLI doc lists 55/85 list price for smartsegment: the doc's number and Studio's displayed number disagree and neither was re-verified by a charge. No CLI mapping verified for 부분별 생성, 파트 보완 or rig presets | 11 |

## Face / hair handling and the 2026-11-06 head direction

Target: a separable head, no eyeballs, open eye sockets, open mouth, teeth as a separate component.

- Studio has **no face/hair/teeth-specific control**. Nothing in the tool rail lets you declare "no eyeballs", "open mouth" or "teeth as separate part".
- The user's own earlier assets in the asset panel include "bald head", "bald anime head", "anime head", "long burgundy hair", "long black hair wig", "bustier top", "black sleeveless jacket", "muscular torso" (img 02, 06). This shows the prompt-per-part workflow (generate each piece as its own model) was already used. The selected "bald anime head" (img 06) visibly has dark hollow eye regions with no eyeball geometry visible, and an open mouth with no visible teeth. This is an appearance in one viewport shot, not a verified geometry check (no wireframe/section view taken).
- Relevant features: (a) prompt-per-part generation (works today, no subscription); (b) 분할 with 정밀 15+ parts could try to split teeth/head, unverified and costs 40; (c) 부분별 생성 might emit parts at generation time, unobserved because subscriber-only. A separable head with a clean open mouth cavity is more reliably done in Blender than via Studio segmentation.
- Hair stays hairdeck-only per plan decision; Studio hair assets (wig models) are not adopted.

## What is still UNOBSERVED and why

- Effect and output of 부분별 생성: subscriber-only, toggle not enabled (enabling is a plan-gated feature; running it would charge).
- Real segmentation/part-completion results and quality on a character: would require clicking 분할 시작 (40 credits) or 파트 보완; not done by rule.
- Credit labels for Smart UV, 파트 보완, 애니메이션, 편집, 해상도 향상, PBR: no label appeared without running; Smart UV and 애니메이션 panels were not opened.
- Rig quality / skeleton bone naming for each preset: would require 자동 리깅 (20). Not needed since rig is Auto Rig Pro only.
- Whether exported FBX/GLB keep separate parts: export confirm not clicked.
- CLI parity for Studio-only features: not testable without charges.

## Adoption verdict

| Stage | Verdict | Reason |
|---|---|---|
| bases | adopt-as-fallback | Generation works signed in (30 credits shown for default), has quad/tri topology and polygon controls, GLB/FBX/OBJ export. CLI is scriptable and already costed, so Studio is for manual one-offs only. |
| faces | adopt-as-fallback (manual, one-off only) | Prompt-per-part head generation worked for the user's own assets, but there is no control for no-eyeball / open sockets / open mouth / separate teeth. The head spec should be built in the project's own pipeline (Blender); Studio output can only be a reference or a starting mesh. |
| garments | adopt-as-fallback | Prompt-per-garment works (bustier top, jacket assets exist). Segment (tri meshes only, 40 credits) and 부분별 생성 (subscriber) are unverified for outfit separation; CLI smartsegment remains the scriptable route. |
| rig | not adopt | User decision: Auto Rig Pro only. Studio rig (humanoid/other, Mixamo/UE5/VRM/Unity presets, 20 credits) was observed but excluded. |

Hair: hairdeck only; no Studio hair adoption.
