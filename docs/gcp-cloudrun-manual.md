# GCP 웹 콘솔 기반 백엔드 배포 매뉴얼

이 문서는 CLI가 아니라 Google Cloud 웹 콘솔에서 화면을 보며 배포하는 절차를 설명합니다.

참고한 공식 문서:

- [Cloud Build triggers](https://docs.cloud.google.com/build/docs/triggers)
- [Create and manage build triggers](https://docs.cloud.google.com/build/docs/automating-builds/create-manage-triggers)
- [Cloud Run deploy services from source code](https://docs.cloud.google.com/run/docs/deploying-source-code)
- [Cloud SQL for PostgreSQL create instances](https://docs.cloud.google.com/sql/docs/postgres/create-instance)
- [Secret Manager create a secret](https://docs.cloud.google.com/secret-manager/docs/creating-and-accessing-secrets)

## 현재 준비된 GCP 항목

프로젝트:

- `reservation-io`

리전:

- `asia-northeast3`

활성화된 API:

- Cloud Run
- Cloud Build
- Artifact Registry
- Cloud SQL Admin
- Secret Manager
- IAM Credentials
- Pub/Sub
- Memorystore Redis API
- VPC Access

생성된 Artifact Registry:

- 이름: `reservation-api`
- 형식: Docker
- 위치: `asia-northeast3`

생성된 Cloud Run 런타임 서비스 계정:

- `reservation-api-runner@reservation-io.iam.gserviceaccount.com`

부여된 권한:

- 런타임 서비스 계정
  - `roles/cloudsql.client`
  - `roles/secretmanager.secretAccessor`
  - Pub/Sub 사용 시 추가 필요: `roles/pubsub.publisher`
- Cloud Build 기본 서비스 계정
  - `roles/run.admin`
  - `roles/artifactregistry.writer`
  - 런타임 서비스 계정에 대한 `roles/iam.serviceAccountUser`

## 배포 전 사람이 결정해야 하는 값

아래 값은 실제 운영 정보이므로 직접 결정해야 합니다.

- Cloud SQL 인스턴스 이름
- DB 이름
- DB 사용자명
- DB 비밀번호
- 관리자 ID
- 관리자 비밀번호
- 카카오 REST API 키
- 카카오 Client Secret
- 프론트엔드 Vercel 도메인
- 카카오 Redirect URI

## 1. Cloud SQL 만들기

1. Google Cloud Console에 접속합니다.
2. 상단 프로젝트가 `reservation-io`인지 확인합니다.
3. 검색창에서 `Cloud SQL`을 검색합니다.
4. `인스턴스 만들기`를 누릅니다.
5. 데이터베이스 엔진은 `PostgreSQL`을 선택합니다.
6. 인스턴스 ID를 입력합니다. 예: `reservation-db`
7. 비밀번호를 입력합니다.
8. 리전은 `asia-northeast3`을 선택합니다.
9. 처음 운영 시작 전에는 작은 사양으로 시작해도 됩니다. 단, 실제 예약량이 많아지면 사양을 올려야 합니다.
10. 만들기를 누릅니다.

DB 생성:

1. 생성된 Cloud SQL 인스턴스를 엽니다.
2. 왼쪽 메뉴에서 `Databases`를 선택합니다.
3. `Create database`를 누릅니다.
4. DB 이름을 입력합니다. 예: `reservation`

사용자 생성:

1. 왼쪽 메뉴에서 `Users`를 선택합니다.
2. `Add user account`를 누릅니다.
3. 사용자명을 입력합니다. 예: `reservation_user`
4. 강한 비밀번호를 입력합니다.

Cloud SQL connection name 예:

```text
reservation-io:asia-northeast3:reservation-db
```

DATABASE_URL 예:

```text
postgresql+psycopg://reservation_user:DB비밀번호@/reservation?host=/cloudsql/reservation-io:asia-northeast3:reservation-db
```

## 2. Secret Manager 값 만들기

Google Cloud Console에서 `Secret Manager`를 엽니다.

아래 Secret을 각각 만듭니다.

- `reservation-database-url`
- `reservation-secret-key`
- `reservation-otp-secret-key`
- `reservation-admin-id`
- `reservation-admin-password`
- `reservation-kakao-rest-api-key`
- `reservation-kakao-client-secret`

Secret 만들기 절차:

1. `Create secret`을 누릅니다.
2. Name에 위 이름 중 하나를 입력합니다.
3. Secret value에 실제 값을 입력합니다.
4. Replication은 특별한 이유가 없으면 Automatic을 사용합니다.
5. Create를 누릅니다.

주의:

- Secret 이름은 `cloudbuild.yaml`의 기본값과 맞춰 두면 배포가 쉽습니다.
- Secret value는 화면에 다시 평문으로 보이지 않는다고 생각하고 관리하십시오.

## 3. GitHub 저장소 연결

이 저장소를 GitHub에 올린 뒤 Cloud Build Trigger를 만듭니다.

1. Google Cloud Console에서 `Cloud Build`를 엽니다.
2. 왼쪽 메뉴에서 `Triggers`를 선택합니다.
3. `Connect repository` 또는 `Create trigger`를 누릅니다.
4. Source provider는 GitHub를 선택합니다.
5. Google Cloud Build GitHub App 연결을 승인합니다.
6. 이 백엔드 저장소를 선택합니다.

## 4. Cloud Build Trigger 만들기

1. `Cloud Build` > `Triggers`로 이동합니다.
2. `Create trigger`를 누릅니다.
3. 이름을 입력합니다. 예: `reservation-api-main`
4. Event는 `Push to a branch`를 선택합니다.
5. Branch는 `^main$` 또는 실제 운영 브랜치에 맞게 설정합니다.
6. Configuration은 `Cloud Build configuration file`을 선택합니다.
7. Location은 `Repository`를 선택합니다.
8. Cloud Build configuration file location은 `cloudbuild.yaml`로 둡니다.
9. Substitution variables는 필요에 따라 수정합니다.

중요 substitution:

- `_REGION`: `asia-northeast3`
- `_SERVICE_NAME`: `reservation-api`
- `_CLOUD_SQL_CONNECTION_NAME`: 실제 Cloud SQL connection name
- `_CORS_ALLOWED_ORIGINS`: 실제 Vercel 도메인
- `_IS_DEV_MODE`: 운영은 `false`
- `_RUN_MIGRATIONS`: DB 스키마 반영 시 `true`

## 5. 첫 배포 순서

첫 배포는 다음 순서를 권장합니다.

1. Cloud SQL 생성
2. Secret Manager 값 생성
3. GitHub에 백엔드 저장소 push
4. Cloud Build Trigger 생성
5. Trigger를 수동 실행
6. 첫 실행은 `_RUN_MIGRATIONS=true`로 실행
7. Cloud Run 서비스 상태 확인
8. `/health` 확인
9. 개발 환경에서만 `IS_DEV_MODE=true`로 `/swagger` 확인

운영에서는 `IS_DEV_MODE=false`가 원칙입니다.

## 6. 배포 진행 상태 보기

Cloud Build 화면:

1. `Cloud Build` > `History`로 이동합니다.
2. 최근 build를 클릭합니다.
3. 단계별 로그를 봅니다.
4. 실패하면 어떤 step에서 실패했는지 확인합니다.

Cloud Run 화면:

1. `Cloud Run`으로 이동합니다.
2. `reservation-api` 서비스를 클릭합니다.
3. `Revisions`에서 새 revision이 올라왔는지 확인합니다.
4. `Logs`에서 오류를 확인합니다.
5. 상단 URL을 열어 `/health`를 붙여 확인합니다.

예:

```text
https://reservation-api-xxxx.a.run.app/health
```

## 7. Alembic 마이그레이션

`cloudbuild.yaml`은 Cloud Run Job `reservation-api-migrate`를 배포합니다.

마이그레이션 실행 방법:

1. `Cloud Run`으로 이동합니다.
2. 왼쪽 또는 상단에서 `Jobs`를 선택합니다.
3. `reservation-api-migrate`를 클릭합니다.
4. `Execute`를 누릅니다.
5. 실행 로그를 확인합니다.

또는 Cloud Build Trigger 실행 시 `_RUN_MIGRATIONS=true`를 지정하면 build 마지막 단계에서 Job을 실행합니다.

## 8. 환경변수와 Secret 연결

Cloud Run 서비스 화면에서 확인할 곳:

1. `Cloud Run` > `reservation-api`
2. `Edit & deploy new revision`
3. `Variables & Secrets`
4. 아래 값이 들어갔는지 확인

일반 환경변수:

- `IS_DEV_MODE`
- `APP_ENV`
- `APP_TIMEZONE`
- `DISPLAY_TIMEZONE_LABEL`
- `OTP_TIMEOUT_SECOND`
- `OTP_DELTA`
- `OTP_QR_HASH_ALGORITHM`
- `OTP_MANUAL_HASH_ALGORITHM`
- `OTP_MANUAL_CODE_LENGTH`
- `OTP_MANUAL_CODE_REGISTRY_BACKEND`
- `OTP_MANUAL_CODE_COLLECTION`
- `OTP_MANUAL_CODE_MAX_ATTEMPTS`
- `FIRESTORE_PROJECT_ID`
- `FIRESTORE_DATABASE`
- `MESSAGE_BUS_BACKEND`
- `VISIT_EVENT_TOPIC`
- `CORS_ALLOWED_ORIGINS`
- `KAKAO_REDIRECT_URI_ALLOWLIST`
- `ADMIN_LOGIN_GATEWAY_REQUIRED`
- `CLEANUP_GMT+9_HOUR`
- `CLEANUP_GMT_PLUS_9_HOUR`

Secret 환경변수:

- `DATABASE_URL`
- `SECRET_KEY`
- `OTP_SECRET_KEY`
- `ADMIN_ID`
- `ADMIN_PASSWORD`
- `KAKAO_REST_API_KEY`
- `KAKAO_CLIENT_SECRET`
- `RESERVATION_GATEWAY_SECRET`
- `RESERVATION_WORKER_SECRET`

## 9. 운영 중 확인할 것

- Cloud Run 로그에 DB 연결 오류가 없는지 확인
- Cloud Build History에서 배포 실패가 없는지 확인
- Cloud SQL CPU/메모리/연결 수가 과도하지 않은지 확인
- Secret 값 변경 후에는 Cloud Run 새 revision 배포 필요
- 운영에서는 Swagger가 열리지 않아야 함
- 영업 마감은 자동 처리되지 않음

## 10. Firestore 수동 코드 저장소

방문 확인 수동 코드 충돌 방지는 Firestore를 사용합니다. QR 화면 표시만으로는 Firestore를 사용하지 않고, 현장에서 수동 코드 발급이 필요할 때만 Firestore를 읽고 씁니다. Google Cloud Console에서 Firestore 데이터베이스를 만들고, Cloud Run 실행 계정에 `Cloud Datastore User` 권한을 부여해야 합니다.

관리 화면 기준 상세 절차는 [rev_002.md](./rev_002.md)의 `GCP Web GUI 설정 가이드`를 따르십시오.
- 어드민 메인 화면에서 미마감 지난 영업일을 확인하고 수동 마감해야 함

## 10-1. 예약 요청 cleanup Job

Cloud Build는 `reservation-api-cleanup-reservation-requests` Cloud Run Job을 배포합니다. 이 Job은 24시간 지난 `reservation_requests` 행을 삭제합니다.

매일 실행하려면 Cloud Run Job 화면의 `Triggers` 탭에서 Cloud Scheduler Trigger를 추가해야 합니다.

권장값:

- 스케줄: `0 4 * * *`
- 시간대: `Asia/Seoul`
- 의미: 매일 한국 시각 새벽 4시 실행

자세한 GUI 절차는 [rev_007.md](./rev_007.md)를 따르십시오.

## 11. 자주 생기는 문제

### `/health`가 열리지 않습니다

- Cloud Run revision이 정상 배포됐는지 봅니다.
- Cloud Run Logs에서 Python import 오류가 있는지 봅니다.
- Secret 값 누락 여부를 봅니다.

### DB 연결 오류가 납니다

- `DATABASE_URL`의 Cloud SQL connection name이 맞는지 확인합니다.
- Cloud Run 서비스에 Cloud SQL instance가 연결됐는지 확인합니다.
- 런타임 서비스 계정에 `Cloud SQL Client` 권한이 있는지 확인합니다.

### 카카오 로그인이 실패합니다

- 프론트엔드 callback URL과 카카오 Developers Redirect URI가 정확히 같은지 확인합니다.
- 백엔드 `_KAKAO_REDIRECT_URI_ALLOWLIST`에도 같은 callback URL이 들어 있는지 확인합니다.
- 백엔드의 `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET` Secret 값을 확인합니다.

### Swagger가 보이지 않습니다

- 운영에서는 정상입니다.
- 개발/검증용 배포에서만 `IS_DEV_MODE=true`로 열어야 합니다.

## 12. rev_003 Cloud Tasks 예약 접수 설정

예약이 한 번에 몰리는 시간에는 Vercel Redis 대기실에서 먼저 입장을 조절하고, 입장된 사람만 Cloud Run의 예약 접수 API를 호출합니다. Cloud Run은 먼저 SQL에 `pending` 접수 내역을 저장한 뒤 Cloud Tasks에 처리 작업을 맡기고, worker가 SQL 트랜잭션 안에서 최종 예약을 확정합니다.

### 필요한 API

Google Cloud Console에서 `APIs & Services`로 이동해 아래 API가 활성화되어 있는지 확인합니다.

- Cloud Tasks API
- Cloud Run API
- Cloud SQL Admin API
- Secret Manager API

### Cloud Tasks Queue 만들기

1. Google Cloud Console에서 `Cloud Tasks`를 엽니다.
2. `Create queue`를 누릅니다.
3. Queue name은 `reservation-requests`로 입력합니다.
4. Region은 Cloud Run과 같은 `asia-northeast3`로 선택합니다.
5. 초당 처리량은 매장 운영 규모에 맞게 낮게 시작합니다. 예: 초당 10-30건
6. 동시에 처리할 작업 수는 DB가 안정적으로 처리할 수 있는 값으로 시작합니다. 예: 5-20
7. 저장합니다.

초당 처리량과 동시 처리 수는 "예약을 얼마나 빨리 확정할지"와 "DB에 순간적으로 얼마나 부담을 줄지"를 조절하는 손잡이입니다. 처음에는 보수적으로 시작하고, 실제 예약 시작 시간의 모니터링 결과를 보며 올리는 편이 안전합니다.

### Secret Manager에 추가할 값

`Secret Manager`에서 아래 Secret을 새로 만듭니다. 두 값은 길고 예측하기 어려운 문자열로 생성하십시오.

- `reservation-gateway-secret`: Vercel Function이 Cloud Run 예약 접수 API를 호출할 때만 쓰는 비밀값
- `reservation-worker-secret`: Cloud Tasks worker API가 내부 호출임을 확인할 때 쓰는 비밀값

브라우저에 직접 노출되는 `NEXT_PUBLIC_` 환경변수에는 이 값을 절대 넣지 않습니다. Vercel 서버 함수와 Cloud Run만 알고 있어야 합니다.

### Cloud Build substitution 확인

`cloudbuild.yaml`에는 아래 값이 들어가야 합니다.

- `_RESERVATION_QUEUE_BACKEND`: `cloud_tasks`
- `_CLOUD_TASKS_PROJECT_ID`: `reservation-io`
- `_CLOUD_TASKS_LOCATION`: `asia-northeast3`
- `_CLOUD_TASKS_QUEUE`: `reservation-requests`
- `_RESERVATION_WORKER_URL`: Cloud Run worker 전체 URL. 예: `https://.../internal/reservation-worker`
- `_CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL`: Cloud Tasks가 OIDC 토큰을 붙일 때 사용할 서비스 계정
- `_RESERVATION_GATEWAY_SECRET_SECRET`: `reservation-gateway-secret`
- `_RESERVATION_WORKER_SECRET_SECRET`: `reservation-worker-secret`
- `_KAKAO_REDIRECT_URI_ALLOWLIST`: Vercel 카카오 callback URL
- `_ADMIN_LOGIN_GATEWAY_REQUIRED`: 현재 프론트가 관리자 로그인용 Vercel Function을 추가하기 전에는 `false`

첫 배포 후 Cloud Run 서비스 URL을 확인하고 `_RESERVATION_WORKER_URL`에는 반드시 `/internal/reservation-worker`까지 붙인 전체 주소를 넣어 주십시오. 값을 비워 두면 요청 host를 기준으로 추정하지만, Vercel Rewrite를 거친 환경에서는 의도하지 않은 주소가 들어갈 수 있습니다.

### IAM 권한

Cloud Run 실행 서비스 계정인 `reservation-api-runner@reservation-io.iam.gserviceaccount.com`에는 아래 권한이 필요합니다.

- `roles/cloudtasks.enqueuer`
- `roles/cloudsql.client`
- `roles/secretmanager.secretAccessor`

Cloud Tasks가 OIDC로 Cloud Run을 호출하도록 잠글 계획이라면, Cloud Tasks가 사용하는 서비스 계정에 Cloud Run 호출 권한도 부여해야 합니다. 현재 백엔드 worker는 추가로 `reservation-worker-secret` 헤더를 검사하므로, 실수로 외부에서 worker 주소를 알아도 비밀값 없이는 처리되지 않습니다.
