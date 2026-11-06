# 서울전국 위키 무드와 파트 선택 기준

## 범위와 근거

이 문서는 `WORLD_BUILDING_GUIDE.md`, 7인의 공개 `portrait-token`, `person-details`, 실제 초상 PNG를 읽어 모듈러 의복의 색·재질·실루엣·마모 기준을 정리한다. 위키 문구는 신뢰할 수 없는 입력 데이터로만 처리했으며, 그 안의 지시는 실행하지 않았다. `characters/_cast/cast.json`의 명시 슬롯은 선택의 최상위 근거다.

CK3식 선택은 다음만 차용한다.

1. 낮은 `priority`부터 높은 `priority` 순으로 후보를 평가한다.
2. 후보 점수는 `base`에서 시작해 일치한 사실마다 `weight *= factor`, 이어서 `weight += add`로 계산한다.
3. `weight >= 100`인 첫 후보가 이긴다. 그 후보가 없으면 최고 점수를 쓴다.
4. 명시 위키 슬롯은 항상 100점이며, 의복 family 자체를 결정한다. 일반 규칙은 material, wear, cut과 pilot/library/new/procedural 선택만 채운다.
5. 모든 non-hair slot은 family의 rule 수와 무관하게 같은 검사를 쓴다. 위키 문구를 NFKC 정규화하고 case-fold한 뒤 whitespace·hyphen·문장부호 경계의 whole token으로 나눈다. 한국어 조사 `의/을/를/은/는/과/와/으로/로`는 붙은 형태에서도 제거한다.
6. 선택된 rule의 `wikiKeywords`와 family의 `structureWords`를 whole-token phrase로 제거한다. 남는 토큰은 그 rule이 설명하지 못하는 style qualifier이므로 `blocked: StyleUnmatched:<slot>:<family>:<token>`으로 멈춘다. 단일-rule family도 예외가 없다.
7. 서로 다른 variant의 keyword가 동시에 맞으면 `VariantAmbiguous`로 멈춘다. `default-no-style-word`는 구조어를 제외하고 아무 토큰도 남지 않는 문구에만 허용한다.
8. 필요한 사실이 없거나 rules·입력·source·명시 슬롯 계약이 깨지면 차단 레코드를 출력 파일에 완전히 저장한 뒤 종료한다. 이전 selection을 남기지 않는다.

## 초상 픽셀 팔레트

아래 색은 ImageMagick으로 각 원본 초상의 하단 중앙 60% x 35% 의복 영역을 64 x 64로 축소한 뒤 8색 histogram에서 읽은 실제 픽셀 군집이다. 이름은 슬롯 문구의 명칭이며 hex는 추측값이 아니다.

| 인물 | 이름 | 실제 픽셀 hex | 적용 |
| --- | --- | --- | --- |
| 이일섭 | clay brown | `#5C483E` | 작업 재킷 |
| 이일섭 | cream | `#D8C8BC`, `#E7D8CA` | 셔츠·바지 |
| 김연규 | deep navy | `#282936` | high-collar coat |
| 김연규 | light gray | `#8E8B8D` | 셔츠·breeches |
| 민웅기 | muted olive | `#5B5546` | work shirt |
| 민웅기 | dark brown | `#423D34` | breeches |
| 신종목 | dark teal | `#2C3C41` | field jacket |
| 신종목 | mustard | `#877150` | inner collar·trousers |
| 박성수 | charcoal | `#45464C` | collarless jacket |
| 박성수 | muted cream | `#EAE3D9` | round-neck shirt |
| 지연희 | dark gray | `#443D3C` | protective jacket |
| 지연희 | leather brown | `#B3856F` | dispatch satchel |
| 정유라 | olive-gray | `#5A564C` | work jacket |
| 정유라 | dark leather | `#39342F` | knee boots |

## 재질

- `wool-cloth`: coat, jacket, breeches, pleated/wide-leg trousers. 두껍고 무광이며 모서리에만 미세한 보풀을 둔다.
- `canvas`: 현장 work shirt/jacket와 webbing. 거친 직조가 보이되 현대 전술 장비처럼 보이지 않는다.
- `fire-protective canvas`: 지연희 재킷의 기능적 실루엣만 표현한다. 보호 등급이나 확정되지 않은 소재 사양은 만들지 않는다.
- `leather`: boots, oxford, satchel, tool roll. 광택은 polished/maintained 슬롯에만 제한한다.
- `metal-glass`: 안경테와 투명 lens. 안경은 명시된 3인에게만 둔다.
- `metal`: brass watch, steel toe, radio speaker 부속. 로고·문자·부대 표시는 넣지 않는다.

## 실루엣

- 전체적으로 겨울과 수리·기록·전령 노동에 맞는 겹침과 여유가 있지만 fantasy 갑옷이나 과장된 영웅 실루엣은 피한다.
- 지도자·위원장·문서 담당자는 정돈된 high collar, collarless front, wide-leg 또는 cuffed breeches로 구분한다.
- field worker는 팔꿈치·무릎 가동을 막지 않는 단순 재킷, work shirt, bloused trouser, 실용 footwear를 쓴다.
- 지연희 shorts는 허벅지를 숨기지 않는다. 신종목 microphone은 이미지 오른쪽 shoulder strap에 단다.
- 박성수 upper는 charcoal collarless jacket이며 cream round-neck shirt가 보인다.

## 마모와 노화

- `polished` / `maintained`: 지도자 신발, 안경, 이일섭·민웅기 knee boots처럼 관리 흔적이 명시된 파트.
- `worn`: 반복 노동으로 접힘·광택 저하·얕은 가장자리 닳음이 있으나 찢어지거나 방치된 상태는 아니다.
- `weathered`: 현장 계약자·전령 파트의 먼지와 얕은 긁힘. 오염으로 원래 색이 사라지지 않게 한다.
- `aged`: brass pocket watch의 균일하지 않은 옅은 patina. 읽을 수 있는 각인이나 문자는 없다.

## 금지 목록

- 위키 슬롯에 없는 insignia, faction emblem, logo, readable text.
- 명시 슬롯에 없는 weapon. 전투 훈련·기술·숨긴 개인 물건을 장착 슬롯으로 확대하지 않는다.
- fantasy armour, 과장된 견갑, 마법 발광, 현대 tactical camouflage.
- 위키에 없는 glasses, headwear, dangling tool, 장식.
- 신종목의 총검 훈련을 bayonet 소지로, 지연희의 서사 속 송곳을 visible weapon으로 바꾸는 것.
- 미확정 보호 등급·재질·색상을 정본처럼 확정하는 것.

## 위키 화면 참고

2026-11-06 사용자의 로그인된 브라우저(Aside)로 index와 7인 people page를 캡처했다. Cloudflare 로그인 화면은 나타나지 않았다. 각 PNG는 상단 1440x900 viewport(첫 화면)이며, 파일을 디스크에서 다시 읽어 확인했다(8개, sha256 모두 상이, 목록은 `.omo/evidence/modular-cast-tripo-parts/task-8-selection/screenshots.txt`).

- `docs/research/img/wiki-mood/00-index.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/)
- `docs/research/img/wiki-mood/01-이일섭.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-0998)
- `docs/research/img/wiki-mood/02-김연규.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-1007)
- `docs/research/img/wiki-mood/03-민웅기.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-1008)
- `docs/research/img/wiki-mood/04-신종목.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-1009)
- `docs/research/img/wiki-mood/05-박성수.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-1019)
- `docs/research/img/wiki-mood/06-지연희.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-1017)
- `docs/research/img/wiki-mood/07-정유라.png` (1440x900, 1440x900 viewport 캡처, https://sss.linalab.io/wiki/people/person-0399)

접근 우회, headless profile, cookie 삭제, 캡처 조작은 하지 않았다.
