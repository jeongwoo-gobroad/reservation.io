# Pokecard Reservation API

포켓몬 카드 구매 예약 시스템의 백엔드 저장소입니다.

현재 백엔드는 Python FastAPI 기반이며, Cloud Run, Cloud SQL for PostgreSQL, Secret Manager, Cloud Build 배포를 전제로 합니다. 이 저장소에는 실제 HTML 화면이나 프론트엔드 빌드 결과물을 두지 않습니다. 프론트엔드는 별도 저장소에서 Vercel로 배포합니다.

## 구성

- `reservation_api/`: FastAPI 백엔드
- `alembic/`: DB 스키마 버전 관리
- `cloudbuild.yaml`: Cloud Build 기반 Cloud Run 배포
- `Dockerfile`: Cloud Run 컨테이너 이미지
- `docs/api.md`: API 명세
- `docs/frontend.md`: 프론트엔드 구현 지침
- `docs/gcp-cloudrun-manual.md`: GCP 웹 콘솔 배포 매뉴얼
- `docs/vercel-manual.md`: Vercel 프론트엔드 배포 매뉴얼
- `docs/admin-manual.md`: 비전문가용 관리자 매뉴얼
- `docs/architecture.md`: 서비스 아키텍처
- `docs/rev_003.md`: Vercel Redis waiting room + Cloud Tasks 예약 접수 구조
- `docs/rev_004.md`: 판매 일자 오픈/마감과 Vercel Redis waiting room 동기화

## FastAPI 확인

실행 앱은 `reservation_api.main:app`입니다.

```powershell
$env:IS_DEV_MODE="true"
$env:DATABASE_URL="sqlite:///./.local/reservation_api.db"
python -c "from fastapi import FastAPI; from reservation_api.main import app; print(isinstance(app, FastAPI), app.title)"
```

## 로컬 실행

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

개발 모드에서 Swagger:

```text
http://127.0.0.1:8080/swagger
```

운영에서는 `IS_DEV_MODE=false`로 Swagger를 닫습니다.

## DB 마이그레이션

```powershell
$env:DATABASE_URL="sqlite:///./.local/reservation_api.db"
.\.venv\Scripts\alembic.exe upgrade head
```

Cloud Run 운영 DB에는 Cloud Run Job `reservation-api-migrate`로 Alembic을 실행합니다.

## 주요 기능

- 카카오 로그인 식별값 기반 사용자 생성
- 관리자 Secret 기반 로그인
- 관리자 로그인은 Secret 기반 ID/PW 인증만 수행하며, IP 기반 자동 밴은 백엔드에서 수행하지 않음
- 판매 일정, 상품 그룹, 상품 재고, 방문 시간대 정원 관리
- 같은 날짜 내 복수 일정 예약
- 일정별 “같은 일자 내 다른 일정과 함께 예약 가능” 정책
- 예약 생성 시 DB transaction/row lock 기반 동시성 방어
- 피크 시간 예약 접수를 위한 `reservation_requests` pending 처리와 Cloud Tasks worker 구조
- TOTP 기반 방문 확인 QR/수동 코드
- 방문 완료와 최근 확인 내역
- 영업일 수동 마감
- 미마감 지난 영업일 조회
- 노쇼 카운트는 사용자별 판매 일자당 1회
- 노쇼 기준 이상 사용자 자동 예약 제한
- Markdown 안내문과 예약 동의 메시지 설정

## 배포

GCP 웹 콘솔 중심 절차는 `docs/gcp-cloudrun-manual.md`를 참고하십시오.

프론트엔드 Vercel 배포는 `docs/vercel-manual.md`를 참고하십시오.
> 최신 변경 문서: `docs/system_0622.md`는 전체 시스템 기준 문서이고, `docs/rev_007.md`에는 예약 요청 cleanup Job과 GCP/Vercel 운영 작업이 정리되어 있습니다.
