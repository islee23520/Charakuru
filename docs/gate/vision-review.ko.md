# 게이트 비전 검토 계약 (vision review)

이 문서는 Charakuru 캐스트 파트 게이트(G0/G1/G3/G4/G5)의 **비전 검토자 프롬프트 계약**이다.
검토자는 이 문서 전문, 검토 대상 보드(`tools/gate/compose-board.py` 산출물), 렌더 디렉터리
(`manifest.json` 포함), 그리고 게이트에 따라 `TRAITS.md` / `cast.json` / `contract.json` /
three-view 참고 이미지를 받고 판정 JSON 하나를 산출한다.

## 1. 독립성 원칙

- 검토자는 항상 **새 세션의 독립 에이전트**다. 해당 자산을 생성한 레인(producing lane)은
  절대 자기 산출물을 검토하지 않는다.
- 두 검토자는 **서로의 판정을 보기 전에** 각자의 판정을 작성한다.
- 게이트마다 검토자 2인. 둘 다 `PASS` 여야 `tools/gate/gate.mjs aggregate`가 `REVIEW_PASS`가 된다.
- 검토자는 판정 JSON 외에 게이트 상태 파일을 직접 수정하지 않는다. 수기로 쓴 `PASS`는 무효다.

## 2. 렌더 계약 <a id="render-contract"></a>

`tools/gate/render-views.py`(`blender -b -P tools/gate/render-views.py -- <in> <out_dir> --preset <gate>`)는
다음 고정 계약(`manifest.json`의 `contract`에 그대로 기록)으로 렌더한다.

| 항목 | 값 |
| --- | --- |
| 카메라 리그 | `gate-fixed-v2`: 50mm 렌즈, 36mm 센서, 1024×1024 RGB PNG |
| 카메라 배치 | 전신 5방향(front/right/back/left/threequarter), 머리 3방향, 클로즈업 12종, 포즈 5종×2각(리깅 자산만) |
| 조명 | `fixed-3point-sun-v1`: key(방위 45°, 고도 50°, 3.0), fill(−40°, 25°, 1.0), rim(195°, 35°, 1.6), 그림자 on |
| 배경 | 중립 회색 sRGB 0.5(픽셀 약 128, linear 0.214) |
| 색 관리 | view transform Standard, look None, exposure 0, gamma 1 |
| 결정성 | 매니페스트에 타임스탬프 없음. 같은 입력은 같은 매니페스트를 낸다. 렌더 파일의 sha256이 매니페스트에 기록된다 |

산출 파일 목록(리깅 자산 30장, 정적 메시 20장 + `manifest.json`):

- 전신: `body-front/right/back/left/threequarter.png` (5)
- 머리: `head-front/right/threequarter.png` (3)
- 클로즈업: `crop-eyes`(눈+속눈썹, 두 눈 소켓를 함께 프레이밍), `crop-lips`(입술+콧구멍),
  `crop-mouthopen`(열린 입 구강; 아래 참고), `crop-ear-left/right`, `crop-neck`,
  `crop-hand-left/right`, `crop-elbow-left/right`, `crop-feet-left/right` (12)
- 포즈(리깅만): `pose-walk/sit/elbow90/elbow140/armsraised × front/threequarter` (10)
  — `pose-sit`에는 고정 45cm 상자가 함께 렌더된다.

`crop-mouthopen` 규칙: 자산에 입 벌림 shape key(VRM `aa` / `jaw_open` 계열)가 있으면 값을 1로
올려 렌더하고 `manifest.json`의 `expressions.mouth_open.applied`가 `true`가 된다. shape key가
없으면 닫힌 입으로 렌더되고 `applied: false`로 기록된다. 이 경우 G3의 구강 항목은
"구강 공동 확인 불가"로 처리하고, 열린 입 검증이 필요하면 자산에 표현식을 요구하는
블로킹 항목으로 기록한다.

glTF 임포터가 `glTF_not_exported` 콜렉션에 넣는 프리뷰 보조 오브젝트는 렌더에서 숨겨지며
매니페스트 `hidden_scaffolding`에 기록된다.

## 3. 판정 산출물

판정은 `tools/gate/verdict.schema.json`을 따르는 JSON 하나다:

```
{ "asset", "gate", "reviewer",
  "renders": [{ "name", "sha256" }, ...],        // manifest.json에서 복사
  "checklist": [{ "id", "pass", "note", "crop" }, ...],
  "identity_score": 1..5,
  "verdict": "PASS" | "FAIL",
  "blocking": ["..."] }
```

- `renders[]`는 검토에 실제로 사용한 렌더 파일의 `name`/`sha256`을 **매니페스트에서 그대로**
  복사한다. 이 값으로 `tools/gate/vision/check-verdict.mjs`가 낡은 판정(stale verdict)을 거부한다.
- 검증: `node tools/gate/vision/check-verdict.mjs <verdict.json> --manifest <render_dir>/manifest.json`.
  exit 0일 때만 유효 판정으로 센다. 스키마 위반(필수 필드 누락 등)·sha 불일치·PASS 모순은
  모두 거부된다("유효하지 않은 판정은 통과로 세지 않는다").
- 렌더 세트 자체의 검증: `node tools/gate/vision/check-render-manifest.mjs <render_dir> --preset <gate>`.

## 4. 게이트별 체크리스트

각 항목 `id`는 아래 제시 값을 그대로 쓴다. 항목을 추가할 수 있지만 제거할 수는 없다.
모든 체크리스트 항목은 `note`에 근거를 한 문장 이상 쓴다.

### G0 — 입력 이미지 (8장)

저작 규칙(`docs/authoring-rules.ko.md`) 준수:

- `g0-eight-images`: 머리 4장(front/left/right/back)과 몸 4장이 별도 파일로 존재하고 모두
  정면 기준 좌우가 일치한다.
- `g0-two-arms-hands`: 팔은 정확히 두 개, 손은 정확히 두 개. 소매 아래 맨팔이 하나 더 보이면 실패.
- `g0-arm-spread`: 옆면 시트에서도 몸통만 옆으로 향하고 두 팔은 끝까지 펴져 손 두 개가 모두 보인다.
- `g0-hairbase-cap`: 머리는 헤어베이스 캡(두개골·헤어라인을 따르는 캡)이다. 긴 머리, 앞머리,
  늘어진 가닥, 머리 장식이 없고 반짝이는 민머리가 아니다. 목·어깨·몸·옷이 머리 시트에 없다.
- `g0-neck-stump-body`: 몸 시트에 머리·얼굴·머리카락이 없고 머리가 끼워지는 짧은 목 끝만 있다.
- `g0-no-ornaments`: 팔 장식·머리 장식이 베이스에 굽혀 있지 않다.

### G1 — 메시 (실루엣·대칭·손·개방 구조)

three-view(`characters/iyeon/three/{front,side,back}.png` 등)와 비교:

- `g1-silhouette`: three-view와 실루엣이 일치한다 (게이트 자산의 뷰 세트 기준).
- `g1-symmetry`: 좌우 대칭이 어긋나지 않았다.
- `g1-fingers`: 손가락이 서로 융합되거나 뭉치지 않았다 (crop-hand-*).
- `g1-eye-sockets-open`: 눈 소켓이 뚫려 있다. 얼굴 메시에 눈구슬이 베이크되어 있지 않다 (crop-eyes).
- `g1-mouth-cavity-open`: 입이 구강으로 뚫려 있다. 얼굴 메시에 이빨이 베이크되어 있지 않다
  (crop-lips / crop-mouthopen).

**인체 비례 (G1 몸/머리 공통)** — 매니페스트 `anatomy.measured` 수치와 함께 판정한다.
판정 기준 수치: head/height(crown-to-chin) **0.128–0.143** (7두신), 신장 **1.55–1.90 m**
(남성 부범위 1.65–1.90), crotch/height **0.45–0.52**, 어깨·허리·무릎·팔꿈치·손·발 비율은
인체 도감 대비 ±20% 이내를 그럴듯함 기준으로 삼고, 좌우 대칭은 **오차 2%** 허용:
- `g1-anatomy-head-canon`: 7두신 캐논 — head/height(head crown-to-chin 기준)가 0.128–0.143 밴드 안에
  들고, 들지 않으면 그 편차를 기술한다.
- `g1-anatomy-height-band`: 신장이 성인 밴드(1.55–1.90 m, 남성 부범위 1.65–1.90)에 들거나,
  벗어나면 스케일 문제로 지적한다.
- `g1-anatomy-proportions`: 어깨·허리·골반(crotch)·무릎·팔꿈치·손·발의 비율이 인체적으로
  그럴듯한가 (crotch/height, shoulder, hand/foot 등 `anatomy.measured` 참조).
- `g1-anatomy-symmetry`: 좌우 대칭 (body-left vs body-right 렌더 비교).

### G3 — 얼굴 정체성

`TRAITS.md`(보이는 특징 목록)과 대조:

- `g3-identity-traits`: TRAITS.md의 항목(눈·홍채·눈썹·코·입·턱·피부)이 유지된다.
- `g3-glasses-as-listed`: 안경은 TRAITS.md에 명시된 경우에만, 명시된 형태로만 있다.
  "No glasses" 인물에 안경이 있으면 실패.
- `g3-neck-ring`: 목 링(공용 목 경계)이 베이스와 형태키에서 동일하다 (crop-neck).
- `g3-eyes-seated`: 별도 눈 구성요소(shared component)가 각 소켓에 안착해 있다.
  눈꺼풀 테 두리에 틈이나 관통이 없다 (crop-eyes).
- `g3-oral-component`: 별도 구강 구성요소(개별 치아·잇몸·혀)가 열린 입 안에 보인다.
  입술이 치아를 관통하지 않는다 (crop-mouthopen).
- `g3-face-morph-keeps-head`: 형태키(얼굴 변형)가 머리 높이(crown-to-chin)를 유지한다 —
  변형 후 7두신 비율이 무너지면 실패 (crop-eyes / head-threequarter, manifest `anatomy`).
- `g3-neck-eye-line`: 목 라인과 눈 라인이 자연스럽다 — 목이 지나치게 길/짧거나 눈선이
  치우치면 실패 (crop-neck / crop-eyes).
- `identity_score` 1–5를 반드시 기록한다 (아래 척도).

### G4 — 의상 파트

- `g4-slot-text`: 의상이 해당 슬롯의 기록된 텍스트(cast.json 슬롯 / selection-rules 문구)와 일치한다.
- `g4-colour-material`: 색과 재질이 선택 기록의 팔레트·재질과 일치한다.
- `g4-no-clipping`: 정자세에서 의상과 몸이 뚫고 나가는 클리핑이 없다.
- `g4-joins-in-poses`: walk/sit/elbow90/elbow140/armsraised 포즈에서 hem·cuff·elbow·knee가
  갈라지거나 파고들지 않는다 (pose-* 렌더).
- `g4-hides`: 해당 슬롯의 `hides` 정점 그룹이 슬롯 착용 시 몸을 올바르게 가린다.
- `g4-garment-prop-scale`: 의상·소품의 스케일이 몸에 맞는다 — 소매 폭, 부츠 크기, 주머니·
  지갑시계 등이 실제 인체 비례 대비 어긋나지 않는다 (pose-* 렌더, manifest `anatomy`).

### G5 — 전신(완성본)

- `g5-whole-person`: 전신이 초상(cast.json portrait)과 같은 인물로 판정된다 (identity_score ≥ 4 필요).
- `g5-eyelash-plane`: 속눈썹이 평면(눈꺼풀에 붙은 단순 평면)으로 어색하게 보이지 않는다 (crop-eyes).
- `g5-normal-triangles`: 노멀 삼각형 아티팩트(얼굴의 삼각형 경계 음영)가 없다 (head-threequarter).
- `g5-lip-highlight`: 입술 하이라이트가 벗어난 곳에 있지 않다 (crop-lips).
- `g5-nostril-offset`: 콧구멍 텍스처가 실제 콧구멍 위치에서 어긋나지 않았다 (crop-lips).
- `g5-eye-oral-mount`: G3의 `g3-eyes-seated`/`g3-oral-component` 기준으로 눈·구강 구성요소의
  장착 상태가 최종본에서도 유지된다 (crop-eyes / crop-mouthopen).
- `g5-anatomy-whole`: 전신 비례 종합 — 7두신 캐논, 신장 밴드, 어깨/골반/사지 비례, 좌우 대칭,
  의상·소품 스케일이 전신에서도 성립한다 (body-* 렌더 전체, manifest `anatomy`).
- `identity_score` 1–5를 반드시 기록한다.

참고: 눈·구강 구성요소의 기준 자산은
`E:/git/seoul-dengoku-tools/ART-ASSETS/avatar-gen/components/{shared-eyes,male-oral,female-oral}.blend`다.

## 5. FAIL 규칙 — 모든 FAIL은 크롭을 인용한다

`pass: false`인 체크리스트 항목은 `crop` 필드에 **실재하는 크롭 파일명**(예: `crop-eyes.png`,
또는 검토자가 직접 만든 추가 크롭의 경로)을 반드시 쓴다. 인체 비례(anatomy) 항목의 FAIL도
마찬가지로 근거가 되는 렌더/크롭을 인용한다. 크롭 인용이 없는 FAIL 판정은 `check-verdict.mjs`가
`CropMissing`으로 거부한다. `blocking[]`에는 왜 다음 단계로 못 가는지를 인간이 읽는 문장으로 쓴다.

## 6. identity_score 척도 (1–5)

| 점수 | 의미 |
| --- | --- |
| 5 | 초상/TRAITS와 같은 인물임이 확신된다 |
| 4 | 같은 인물로 판단된다 (사소한 차이는 있음) |
| 3 | 판단이 갈린다 (다른 인물로도 볼 수 있음) |
| 2 | 닮았지만 같은 인물로 보기 어렵다 |
| 1 | 다른 인물이다 |

**PASS 조건**: 모든 체크리스트 항목 `pass: true` **그리고** G3/G5에서 `identity_score ≥ 4`.
하나라도 어기면 `FAIL`이어야 하며, `PASS`로 쓰면 검증기가 `VerdictContradiction`으로 거부한다.

## 7. 불일치 규칙 — 제3의 신선 검토자

두 검토자의 판정이 갈리면(하나 `PASS`, 하나 `FAIL`, 또는 유효 판정 간 identity_score 경계로
집계가 불가), **제3의 신선한 검토자**가 같은 계약으로 독립 판정한다. 최종 판정은 **3인 중
2인의 다수결**이며, 셋 모두 상호 결과를 보기 전에 판정한다.

## 8. 재시도 규칙

자산당 **최대 2회 재생성**까지만 허용되고, 재생성은 크레딧 장부(tools/cast/credit-ledger.mjs
`check`, reserve 규칙) 안에서 이뤄진다. 2회 재생성 후에도 FAIL이면 재시도하지 말고
`blocking[]`과 함께 게이트를 `FAIL`로 올리고 오케스트레이터에게 상위 조정(모델 교체,
규칙 변경, 사용자 결정)을 요청한다.

## 9. 금지 사항

- 생성 레인의 자기 검토 금지 (1항).
- 판정 JSON 수기 조작·게이트 상태 직접 수정 금지.
- `check-verdict.mjs` exit 0이 아닌 판정을 통과로 세는 것 금지.
- 게이트가 `PASS`(사용자 confirm 포함)가 아니면 그 산출물을 소비하는 단계가 시작되어선 안 된다
  (`tools/gate/gate.mjs require`).
