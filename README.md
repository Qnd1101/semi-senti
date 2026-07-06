# Semi Senti

<div align="center">

**반도체 특화 주가 감성 분석 · 다중 관점 매매 시그널 서비스**

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

반도체 도메인 특화 NLP 감성 분석 · 단기/중기/장기 3관점 시그널 · Gemini 설명 가능한 AI 근거
**FastAPI 백엔드 + Next.js 14 대시보드 + 로컬 PostgreSQL**

</div>

---

## 📌 한눈에 보기

- **반도체 15종목** 시세 스크리너 + 종목 상세(홈·차트·뉴스·재무) 대시보드
- **단기/중기/장기** 3관점 매매 시그널(BUY/SELL/HOLD)을 독립 산출
- **반도체 특화 NLP**(KoNLPy + 도메인 사전)로 뉴스 감성 점수화(보수적 정규화)
- **펀더멘털 밴드**(최근 거래가 분위 + 재무)로 적정가 비교
- **Gemini 근거** 생성(+ 실패 시 규칙 기반 폴백)
- **TradingView Lightweight Charts v5** 캔들 차트(크로스헤어 가격 툴팁·일/주/월)
- 데이터는 모두 **로컬 PostgreSQL**에 저장(증권사 연동·자동매매 없음)

---

## ✅ 사전 요구사항

| 항목 | 버전 | 필수 | 비고 |
|------|------|:----:|------|
| Python | **3.12** | ✅ | 백엔드·수집·NLP |
| PostgreSQL | **15+** | ✅ | 로컬 단일 인스턴스 |
| JDK | **1.8+** | ✅ | KoNLPy 구동용 — 설치 후 **JAVA_HOME 환경변수 설정 필수** |
| Node.js | **20 LTS+** | ✅ | Next.js 대시보드 |
| RAM | 8GB+ | 권장 | 형태소 분석·대량 연산 |

> Windows 기준으로 안내합니다. macOS/Linux도 명령만 각 OS에 맞게 바꾸면 동일하게 동작합니다.
> PowerShell에서 한국어가 깨져 보이면 파일 손상이 아니라 콘솔 출력 인코딩 문제일 수 있습니다. `chcp 65001` 실행 후 새 터미널에서 다시 확인하거나, VS Code처럼 UTF-8을 지원하는 편집기로 열어 확인하세요.

---

## 🚀 빠른 시작 (Windows, 약 10분)

### 1) 저장소 클론

```bat
git clone https://github.com/Qnd1101/semi-senti.git
cd semi-senti
```

### 2) PostgreSQL 준비 (로컬)

로컬 PostgreSQL 15가 설치되어 있다고 가정합니다. 관리자(`postgres`) 계정으로 접속해 **전용 롤·DB**를 만듭니다.

```bat
psql -U postgres -h localhost -p 5432 -c "CREATE ROLE semisenti LOGIN PASSWORD 'semisenti';" -c "CREATE DATABASE semisenti OWNER semisenti;"
```

> 이미 `semisenti` DB가 있고 소유자만 다르면: `psql -U postgres -d semisenti -c "ALTER DATABASE semisenti OWNER TO semisenti; ALTER SCHEMA public OWNER TO semisenti;"`

### 3) 환경 변수 (.env)

```bat
copy .env.example .env
```

`.env`에서 아래 값을 채웁니다. **DB 접속은 기본값 그대로** 두면 위에서 만든 롤과 일치합니다.

```ini
DATABASE_URL=postgresql://semisenti:semisenti@localhost:5432/semisenti

# 외부 API 키 (뉴스·재무·AI 근거에 필요)
OPEN_DART_API_KEY=         # https://opendart.fss.or.kr/  (재무·공시)
NAVER_CLIENT_ID=           # https://developers.naver.com/ (뉴스)
NAVER_CLIENT_SECRET=
GEMINI_API_KEY=            # https://ai.google.dev/        (AI 근거, 선택)

# 감성 정규화 상수 (클수록 보수적, 권장 30)
SENTIMENT_NORMALIZATION_K=30
```

> **주가(차트)는 pykrx 기반이라 키가 필요 없습니다.** DART·네이버 키가 없으면 재무·뉴스 수집만 건너뛰고 주가·기존 데이터는 정상 동작합니다.

### 4) 백엔드 (FastAPI, 포트 8001)

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .           REM semi_senti 패키지 설치 (src-layout, 최초 1회 필수)

python db_seed.py          REM 스키마 생성 + 기준 종목 등록 (최초 1회)
python -m semi_senti.api    REM FastAPI 기동 → http://localhost:8001
```

- API 문서: http://localhost:8001/docs
- 헬스 체크: http://localhost:8001/health

> PowerShell에서 가상환경 활성화가 막히면: `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` 후 `.venv\Scripts\Activate.ps1`. 또는 cmd에서 `.venv\Scripts\activate.bat`.

### 5) 프론트엔드 (Next.js, 포트 3000)

새 터미널에서:

```bat
cd web
npm install
copy .env.local.example .env.local   REM NEXT_PUBLIC_API_BASE_URL=http://localhost:8001
npm run dev                           REM → http://localhost:3000
```

브라우저에서 **http://localhost:3000** 접속 → 좌측 사이드바에서 시세 스크리너/종목을 선택하면 대시보드가 뜹니다.

---

## 📥 데이터 수집

### ⚡ 전체 자동 수집 (한 번에 — 권장)

반도체 15종목의 **주가(전체이력) · 재무(DART) · 뉴스(2년치) · 감성분석**을 현재 운영 수준으로 한 번에 채웁니다:

```bat
collect_all_data.bat
```

> `.env`에 DART·네이버 키가 있어야 재무·뉴스까지 채워집니다(주가는 키 불필요). 뉴스 2년치 + 감성분석 포함이라 수 분 소요됩니다. 내부적으로 `scripts/seed_all_data.py`를 실행합니다.

### 개별 수집

`db_seed.py`는 스키마와 기준 종목만 등록합니다. 실데이터(주가·재무·뉴스)는 아래로 채웁니다.

```bat
REM 주가(차트) — 키 불필요
python -m semi_senti.cli collect price --stock-code 005930 --market KOSPI --force

REM 재무·공시(DART 키 필요) + 뉴스(네이버 키 필요) 통합 동기화
REM PowerShell 에서는 curl 이 Invoke-WebRequest 별칭이므로 curl.exe 로 호출
curl.exe -X POST "http://localhost:8001/api/sync/005930?force=true"
REM (PowerShell 네이티브) Invoke-RestMethod -Method Post -Uri "http://localhost:8001/api/sync/005930?force=true"
```

- 백엔드 기동 시 `LiveDataPipeline`이 기본 종목을 자동 동기화하고, 이후 주기 폴링(기본 60초)으로 최신화합니다.
- 뉴스를 더 많이 수집하려면 네이버 검색 API의 페이징(종목당 최대 1,000건)을 활용합니다.

---

## 🧩 주요 기능

| 기능 | 설명 |
|------|------|
| **반도체 시세 스크리너** | 큐레이션 15종목을 현재가·등락률·거래량·1주/1개월/1년 수익률로 정렬 비교 |
| **3관점 시그널** | 단기/중기/장기 가중치 모델로 BUY/SELL/HOLD 독립 산출(±25 임계) |
| **감성 분석** | KoNLPy + 반도체 특화 사전, `100·tanh(raw/k)` 정규화(보수성은 `k`로 조절) |
| **펀더멘털 밴드** | 최근 250거래일 주가 분위 우선 + 재무 PER×EPS 보조로 적정가 산출 |
| **캔들 차트** | lightweight-charts v5 — 크로스헤어 OHLC 툴팁, 일/주/월 토글, 매매 마커 |
| **뉴스 + 기간 필터** | 7/30/90일 필터(기본 30일), 긍정/중립/부정 색상, 빈 구간 폴백 |
| **AI 근거** | Gemini 구조화 프롬프트 → 근거 카드, 실패 시 규칙 기반 폴백 |
| **관리자·알림** | 종목 CRUD·DB 신선도 모니터링, 텔레그램 알림 |

---

## 🏗️ 아키텍처

```
[수집] DART·네이버·pykrx ──▶ [PostgreSQL 허브] ──▶ [분석] 감성·밴드·시그널·Gemini
                                   │
                                   ▼
              [FastAPI Snapshot/Screener/News/Chart API] ──▶ [Next.js 14 대시보드]
```

**대표 API**

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/snapshot/{code}` | 종목 통합 스냅샷(재무·감성·3관점·근거·밴드) |
| GET | `/api/screener/semiconductor?sort=&order=` | 반도체 15종목 스크리너 |
| GET | `/api/chart/{code}/candles?interval=1d|1wk|1mo` | OHLCV 캔들 |
| GET | `/api/news/{code}?days=&limit=` | 뉴스(기간 필터·감성 방향) |
| POST | `/api/sync/{code}?force=true` | 종목 동기화(주가·재무) |

---

## 📁 디렉토리 구조

```
semi-senti/
├─ src/semi_senti/
│  ├─ collector/   # DART·네이버·pykrx 수집 (price/news/dart)
│  ├─ engine/      # sentiment · signal · band · reasoning · divergence · cycle
│  ├─ pipeline/    # LiveDataPipeline (기동 동기화 + 주기 폴링)
│  ├─ db/          # control · schema (PostgreSQL)
│  ├─ api/         # main · screener (FastAPI)
│  ├─ admin/       # 종목 CRUD · 모니터링
│  ├─ notifier/    # 텔레그램
│  ├─ data/        # sector_universe(반도체 15종목) · default_stocks
│  └─ config/      # settings (가중치·임계값·k·환경변수)
├─ web/            # Next.js 14 (components/dashboard-c, lib/dashboard-c)
├─ db_seed.py      # 스키마 생성 + 기준 종목 등록
├─ requirements.txt
└─ run_windows.bat / run_linux.sh
```

---

## 🔧 트러블슈팅

| 증상 | 해결 |
|------|------|
| `connection refused :5432` | PostgreSQL 서비스 실행 확인 (`net start postgresql-x64-15`), `.env`의 `DATABASE_URL` 포트 확인 |
| `password authentication failed "semisenti"` | 2)의 `CREATE ROLE` 실행 여부 확인. 비밀번호는 `semisenti` |
| `No module named semi_senti` | 가상환경에서 `pip install -e .` 실행 (src-layout 패키지 설치 누락) |
| `Java gateway process exited` / `No jvm.dll found` (KoNLPy) | JDK 1.8+ 설치 후 **`JAVA_HOME`을 JDK 루트로 설정**하고 새 터미널에서 실행. 미설정 시 정규식 폴백으로 동작하나 감성 정확도 저하 |
| 포트 8001/3000 충돌 | `netstat -ano | findstr :8001` 후 `taskkill /PID <PID> /F`, 또는 `.env`의 `API_PORT` 변경 |
| 뉴스가 너무 긍정 일색 | `.env`의 `SENTIMENT_NORMALIZATION_K` 값을 키우면 보수적으로(권장 30) |

---

## 🛡️ 보안 · 범위

- `.env`(API 키 포함)는 `.gitignore` 처리 — 커밋 금지
- 증권사 계좌 연동·자동 매매는 **범위에서 원천 제외** (투자 보조 도구)
- 모든 데이터는 로컬 PostgreSQL에 저장

## 📄 License

MIT License — [LICENSE](LICENSE) 참조
