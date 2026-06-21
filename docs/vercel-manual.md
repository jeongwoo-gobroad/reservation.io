# Vercel 프론트엔드 배포 매뉴얼

이 저장소는 백엔드 전용입니다. 프론트엔드 HTML/CSS/JS 또는 프론트엔드 프레임워크 코드는 별도 GitHub 저장소에서 관리하고 Vercel에 연결하십시오.

## 1. 프론트엔드 저장소 만들기

1. GitHub에서 새 저장소를 만듭니다.
2. `docs/frontend.md`와 `docs/api.md`를 기준으로 화면을 구현합니다.
3. API 주소는 환경변수로 관리합니다.

권장 환경변수:

```text
BACKEND_API_BASE_URL=https://reservation-api-xxxx.a.run.app
RESERVATION_GATEWAY_SECRET=...
WAITING_ROOM_ENABLED=true
WAITING_ROOM_MAX_ACTIVE=...
WAITING_ROOM_LEASE_SECONDS=60
WAITING_ROOM_POLL_SECONDS=2
```

`BACKEND_API_BASE_URL`과 `RESERVATION_GATEWAY_SECRET`은 Vercel Function 서버 코드에서만 사용합니다. 브라우저에 노출되는 `NEXT_PUBLIC_` 값으로 만들지 마십시오.

## 2. Vercel 프로젝트 생성

1. [Vercel](https://vercel.com)에 로그인합니다.
2. `Add New...` > `Project`를 선택합니다.
3. GitHub 계정을 연결합니다.
4. 프론트엔드 저장소를 선택합니다.
5. Framework Preset을 프로젝트에 맞게 고릅니다.
   - 순수 정적 HTML이면 `Other`
   - Next.js면 `Next.js`
6. Build Command와 Output Directory를 설정합니다.
7. Environment Variables에 Vercel Function용 백엔드 주소, gateway secret, waiting room 설정을 넣습니다.
8. Deploy를 누릅니다.

## 3. 백엔드 CORS 설정

Vercel 배포가 끝나면 도메인이 생깁니다.

예:

```text
https://pokecard-reservation.vercel.app
```

이 값을 백엔드 Cloud Build substitution `_CORS_ALLOWED_ORIGINS`에 넣어야 합니다.

예:

```text
_CORS_ALLOWED_ORIGINS=https://pokecard-reservation.vercel.app
```

## 4. 카카오 Redirect URI

카카오 Developers 콘솔에 프론트엔드 callback URI를 정확히 등록해야 합니다.

예:

```text
https://pokecard-reservation.vercel.app/oauth/kakao/callback
```

프론트엔드는 이 callback에서 받은 `code`와 같은 `redirect_uri`를 `POST /auth/kakao/exchange`에 전달해야 합니다.

## 5. 프론트엔드 필수 화면

이용자:

- 카카오 로그인
- 닉네임 설정
- 로그인 안내문 확인
- 예약 가능한 일정
- 내 예약
- 방문 확인증

관리자:

- 관리자 로그인
- 관리자 메인
- 마감이 필요한 지난 영업일
- 방문 확인
- 판매 일정 만들기
- 현재 예약 현황
- 노쇼/예약 제한
- 안내문 관리
- 예약 동의 메시지 관리
- 당일 영업 마감

## 6. 배포 후 확인

1. Vercel URL을 엽니다.
2. 카카오 로그인 버튼이 보이는지 확인합니다.
3. 브라우저 개발자 도구 Network 탭에서 예약 생성/수정/취소와 관리자 오픈/마감이 Vercel Function 경로를 거치는지 확인합니다.
4. CORS 오류가 있으면 백엔드 `_CORS_ALLOWED_ORIGINS`를 확인합니다.
5. 운영 백엔드는 `IS_DEV_MODE=false`여야 합니다.

## 7. Vercel Function이 필요한 경로

아래 경로는 단순 Rewrite로 Cloud Run에 넘기지 말고 Vercel Function으로 구현하십시오.

- `POST /api/reservation-requests`
- `PATCH /api/reservations/{reservation_uuid}`
- `DELETE /api/reservations/{reservation_uuid}`
- `POST /api/admin/schedule-dates/{sale_date}/open`
- `POST /api/admin/schedule-dates/{sale_date}/close`

역할:

- 예약 생성/수정/취소 Function은 Vercel Redis waiting room을 확인한 뒤 Cloud Run에 `X-Reservation-Gateway-Secret`을 붙여 호출합니다.
- 관리자 오픈/마감 Function은 Cloud Run 응답의 `waiting_room_sync`를 읽어 Vercel Redis `wr:open:{sale_date}`를 갱신합니다.
- 자세한 구현 지침은 `docs/rev_003.md`와 `docs/rev_004.md`를 기준으로 합니다.
