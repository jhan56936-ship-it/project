# BLOCK RIVALS

브라우저 1인칭 팀 슈팅 (빨강 vs 파랑, 30킬 선취 승리). 링크만 보내면 친구가 어디서든 접속해 함께 플레이.

## 로컬 실행

```bash
npm install          # 최초 1회
npm run build        # 클라이언트 빌드 (client/dist 생성)
npm start            # http://localhost:3000
```

개발 모드(클라이언트 핫리로드): 서버 `npm start` + 별도 터미널에서 `cd client && npm run dev` → http://localhost:5173

## 테스트

```bash
npm test                          # 서버 단위 테스트 (vitest)
node test-bot.js 4                # 헤드리스 봇 4명 투입 (서버 켠 상태에서)
```

## 조작

WASD 이동 · 마우스 조준 · 좌클릭 발사 · 스페이스 점프 · Shift 달리기 · R 재장전 · Tab 스코어보드

## 무기

| 무기 | 특징 |
|------|------|
| 돌격소총 | 밸런스형 (20데미지, 30발) |
| 샷건 | 근거리 펠릿 8발 |
| SMG | 빠른 연사 (40발) |
| 스나이퍼 | 몸샷 한 방 (5발) |

## 배포 (Render 무료)

저장소 루트의 `render.yaml` 블루프린트 사용: dashboard.render.com → New → Blueprint → 이 저장소 선택.
또는 수동: New → Web Service, Branch `block-rivals`, Root Directory `game`, Build `npm install && npm run build`, Start `npm start`, env `NODE_VERSION=22`.

## 구조

- `server/` — Node.js 권위 서버 (express + ws, 20Hz 틱): 명중·킬·점수·리스폰을 서버가 판정
- `client/` — Three.js + Vite + TypeScript: 1인칭 조작, 스냅샷 보간, HUD
- `shared/` — 무기 스탯·맵 데이터 (서버/클라 공용)
