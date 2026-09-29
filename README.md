# parking-api-qa-lab

작은 주차 예약·ETA API를 직접 만들고, 그 API를 **pytest**로 검증하고, **직접 만든 Mock 서버**로 외부 경로 API의 오류·지연·계약 변경을 통제해 연동 로직을 검증하고, **k6**로 합격 기준(threshold)이 있는 부하 테스트를 돌리고, 이를 **GitHub Actions**에서 반복 실행하는 QA 자동화 실습 저장소입니다.

> 개인 학습 프로젝트입니다. 모든 코드·데이터는 이 저장소 안에서 직접 만들었습니다.
> 성능 수치는 아래 명시한 환경의 단일 인스턴스 SUT 측정값이며 운영 환경 성능을 뜻하지 않습니다.

![ci](https://github.com/tmkim0512-max/parking-api-qa-lab/actions/workflows/ci.yml/badge.svg)

## 요건 ↔ 산출물

| 역량 | 산출물 | 볼 곳 |
|---|---|---|
| API 자동화 테스트 케이스 | 정상·오류·경계·계약·동시성 22개 케이스 (본문 값·부수효과·에러 코드까지 단언) | [`tests/test_api.py`](tests/test_api.py) |
| API Mocking 도구 **개발** | 스텁(순차 응답·지연·깨진 본문)·요청 기록·reset 제어 API를 가진 Mock 서버 + 자체 테스트 4개 | [`mockserver/server.py`](mockserver/server.py), [`tests/test_mock.py`](tests/test_mock.py) |
| API Mocking **활용** | Mock으로 외부 경로 API의 500 지속·503→200·타임아웃 초과 지연·필드명 변경을 재현해 재시도·폴백·계약 검사 검증 (5개) | [`tests/test_eta.py`](tests/test_eta.py) |
| 비기능(부하) 테스트 | k6 부하 모델 + threshold 합격 기준, 로컬 3회 실측으로 기준 산정 | [`load/load.js`](load/load.js), 아래 "성능" |
| CI | push·PR마다 lint → 환경 기동 → pytest → k6 smoke 게이트, 실패해도 리포트·로그 업로드 | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |
| 테스트가 결함을 잡는가 | `SUT_DEFECTS=on`으로 심은 결함 3개가 지정 테스트에서 실패하는지 대조 | 아래 "결함 탐지 대조" |

## 구성

```
sut/app.py            FastAPI SUT: /health /lots/{id} /reservations(POST·GET·DELETE) /eta
sut/route_client.py   외부 경로 API 클라이언트: 타임아웃 2s, 5xx·연결 실패 시 1회 재시도, 계약 검사, 직선거리 폴백
mockserver/server.py  Mock 서버 (제어 API는 파일 상단 docstring)
contracts/            외부 경로 API 응답 계약 (JSON Schema)
tests/                test_api.py · test_eta.py · test_mock.py
load/load.js          k6: PROFILE=smoke | load
load/results/         로컬 부하 측정 원본 JSON
scripts/stack.sh      SUT(:8080) + Mock(:8081) 기동·헬스체크·종료 (로컬·CI 공용)
```

## 실행

요구: Python 3.12, [uv](https://docs.astral.sh/uv/), [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) (부하 테스트만).

```bash
uv sync --python 3.12
scripts/stack.sh up                   # 환경 기동 (헬스체크 실패 시 "ENVIRONMENT FAILURE"로 종료)
uv run pytest -v                      # 31개
k6 run -e PROFILE=smoke load/load.js  # 30초, threshold 위반 시 exit != 0
k6 run -e PROFILE=load  load/load.js  # 약 3분
scripts/stack.sh down
```

결함 모드: `SUT_DEFECTS=on scripts/stack.sh up && uv run pytest`

## 테스트 설계 요약

- **경계**: 예약 시간 29분(거부)/30분/24시간(허용)/24시간+1분(거부), 용량 10번째 허용·11번째 409, 위도 ±90 허용·90.0001 거부.
- **동시성**: 용량 10인 주차장의 같은 시간대에 30개 요청을 병렬로 보내 정확히 10개만 201인지 확인.
- **Mock 시나리오**: 상태 코드뿐 아니라 응답의 `source`(route_api/fallback)·`reason`, Mock 요청 기록으로 본 **재시도 횟수**, 타임아웃 시 **전체 소요 시간 상한**(< 4.5s)을 단언.
- **격리**: 테스트마다 겹치지 않는 예약 시간대를 쓰고(실행마다 무작위 기준점), Mock은 테스트마다 reset. 같은 스택에 연속 재실행해도 결과가 같은 것을 확인했습니다(아래).

## 결함 탐지 대조 (로컬 실행)

| ID | 심은 결함 (`SUT_DEFECTS=on`) | 실패한 테스트 | 실패 메시지 |
|---|---|---|---|
| D1 | 정확히 24시간 예약을 거부 (`>` 대신 `>=`) | `test_duration_boundaries[24h]` | `assert 422 == 201` |
| D2 | 용량 확인과 저장 사이 lock 없음 | `test_concurrent_reservations_never_exceed_capacity` | `assert 30 == 10` (30건 모두 201) |
| D3 | 외부 API 5xx에서 재시도·폴백 없이 500 | `test_upstream_500_retries_once_then_falls_back`, `test_upstream_503_then_200_recovers_on_retry` | `assert 500 == 200` |

- 결함 off: `31 passed` (같은 스택에 3회 연속 동일)
- 결함 on: `4 failed, 27 passed` — 실패는 위 4개뿐, 3회 연속 동일
- D2는 경합 창을 재현 가능하게 넓히려고 결함 모드에서만 50ms 지연을 넣습니다. 순차 용량 테스트(`test_capacity_boundary_10th_ok_11th_full`)는 D2에서 통과합니다 — 동시성 결함은 동시성 테스트로만 잡힌다는 대조입니다.
- 결함 대조는 로컬에서만 확인했습니다. CI에는 별도 결함 잡을 두지 않았습니다.

## 성능 (k6)

부하 모델(`PROFILE=load`): `ramping-arrival-rate`: 시나리오 시작률 초당 10→50회 1분 상승 후 초당 50회 2분 유지(HTTP 요청 처리량은 실행 평균 약 51.8~52.1건/초, 예약 시나리오는 요청 2개). p95는 상승 구간 포함 전체 실행 집계. 트래픽 비율 주차장 조회 70% / 예약+취소 20% / ETA 10%. Mock 경로 API 지연 200ms 고정. 예약은 생성 직후 취소해 만석(정상 409)이 성능 실패로 섞이지 않게 했습니다.

**합격 기준**: 에러율 < 1%, check 성공률 > 99%, p95 — 조회 < 20ms, 예약 < 20ms, ETA < 300ms.
산정 근거: 로컬 3회의 최악 p95(조회 3.55ms, 예약 4.03ms)의 약 5배, ETA는 Mock 고정 지연 200ms + 100ms. 첫 CI 실행(smoke)에서 기준 안에 들어왔습니다(아래 CI 러너 측정값).

### 로컬 머신 측정값

환경: Apple M4 (10코어) · 24GB RAM · macOS 26.3 · Python 3.12.13 · uvicorn 워커 1 · k6 v1.3.0 · SUT·Mock·k6가 같은 머신 · 측정일 2026-09-29

| 실행 | 요청 수 | 처리량 | 에러율 | 조회 p50 / p95 | 예약 p50 / p95 | ETA p50 / p95 | 판정 | 원본 |
|---|---|---|---|---|---|---|---|---|
| 1 | 9,335 | 51.8/s | 0% | 0.90 / 2.45ms | 1.14 / 2.91ms | 210.2 / 216.8ms | 기준 산정용 | [json](load/results/local-load-run1.json) |
| 2 | 9,355 | 52.0/s | 0% | 0.99 / 3.40ms | 1.36 / 4.03ms | 210.2 / 217.0ms | 기준 산정용 | [json](load/results/local-load-run2.json) |
| 3 | 9,389 | 52.1/s | 0% | 0.92 / 3.55ms | 1.11 / 4.01ms | 207.9 / 215.9ms | 기준 산정용 | [json](load/results/local-load-run3.json) |
| 확인 | 9,377 | 52.0/s | 0% | 1.05 / 4.65ms | 1.32 / 5.36ms | 208.8 / 218.4ms | 통과 (최종 기준) | [json](load/results/local-load-confirm.json) |

- 1~3회는 기준을 정하려고 잠정 기준(50/50/400ms)으로 돌렸으므로 원본 JSON의 `thresholds` 판정도 잠정 기준에 대한 것입니다. 최종 기준으로는 "확인" 실행 1회를 돌려 통과했습니다.
- 게이트가 실제로 떨어지는지: Mock 스텁을 비운 상태로 smoke를 돌리면 ETA check가 91.63%로 떨어져 k6가 exit 99로 종료하는 것을 확인했습니다.

### CI 러너 측정값

환경: GitHub-hosted `ubuntu-latest`(ubuntu-24.04, 4 vCPU) · Python 3.12.3 · k6 v2.3.0 · SUT·Mock·k6가 같은 러너 · 측정일 2026-09-29

| 실행 | 프로파일 | 요청 수 | 처리량 | 에러율 | check | 조회 p50 / p95 | 예약 p50 / p95 | ETA p50 / p95 | 판정 |
|---|---|---|---|---|---|---|---|---|---|
| [run 36533642605](https://github.com/tmkim0512-max/parking-api-qa-lab/actions/runs/36533642605) | smoke (1 VU · 30s) | 1,553 | 51.7/s | 0% | 100% (1,552/1,552) | 0.52 / 0.77ms | 0.73 / 1.00ms | 221.0 / 230.3ms | 통과 |

- 같은 run에서 lint 통과, pytest `31 passed`. 원본은 run의 `test-reports` 아티팩트(`k6-smoke.json`, `junit.xml`).
- CI는 smoke 프로파일만 자동 실행합니다. 3분 load 프로파일은 CI에서 아직 돌리지 않았으므로 위 로컬 load 값과 직접 비교하지 않습니다.

## CI

`ci.yml` (push · PR · 수동 실행):
lint → **환경 기동(별도 단계)** → pytest(JUnit XML) → k6 smoke 게이트 → (수동 실행 시 선택) k6 load → 항상: 서비스 로그·Mock 요청 기록·리포트 업로드, Job Summary(통과/실패 수, 실패 테스트 이름, k6 threshold 표).
자동 재시도(rerun)는 쓰지 않습니다 — 플래키를 초록으로 숨기지 않기 위해서입니다.

검증 상태: GitHub Actions에서 실행해 통과했습니다([run 36533642605](https://github.com/tmkim0512-max/parking-api-qa-lab/actions/runs/36533642605), job `test` 47초).

## 한계

- SUT는 메모리 저장소 단일 프로세스입니다. DB·인증·다중 인스턴스는 범위 밖입니다.
- 부하 수치는 SUT·Mock·k6가 한 머신에서 자원을 나눠 쓴 결과이고, 절대 성능이 아니라 **회귀 감지용 기준선**입니다.
- Mock 서버는 method+path 정확 일치만 지원합니다(쿼리·헤더 매칭, 연결 끊기 fault 없음).
- UI 테스트는 다루지 않습니다.
