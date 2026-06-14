# Predictrix-Forex Signal App

> ВАЛЮТЫН ЗАХ ЗЭЭЛИЙН АРИЛЖААНЫ ДОХИО ҮҮСГЭХ СИСТЕМ

| | |
| **Зохиогч / Author** | Мөнхсүлд Мөнхдорж (М.Мөнхдорж) |
| **Оюутны дугаар / Student ID** | s21c033b |
| **Удирдагч багш / Supervisor** | Н.Соронзонболд |
| **Тэнхим / Department** | КОМПЬЮТЕРЫН УХААНЫ ТЭНХИМ |
| **Сургууль / College** | Шинэ Монгол Технологийн Коллеж |
| **Он / Year** | 2026 |

**Guides / Заавар:** [Setup & reproduction](docs/SETUP.md) · [Configuration & secrets](docs/CONFIGURATION.md) · Env templates: [`backend/config/.env.example`](backend/config/.env.example), [`mobile_app/.env.example`](mobile_app/.env.example)

## English

Predictrix is a mobile + backend forex signal system focused on **EUR/USD live signal generation** using a locked production ML model.

### Current Version Scope

- Mobile app: React Native (Expo), version 0.4.5
- Backend: Flask API + MongoDB
- Signal model: Multi-timeframe GBDT ensemble
- Runtime policy: **single active model only**

### Key Features

- Live EUR/USD trading signals (BUY / SELL / HOLD) with a confidence score
- Multi-timeframe GBDT ensemble prediction engine
- User authentication with JWT access + refresh tokens
- Push notifications for newly generated signals (Expo push)
- Scheduled background jobs for automatic signal generation and market analysis
- Persisted signal history in MongoDB
- In-app help and legal screens (privacy policy & terms of service)

### Single-Model Runtime Policy

- Backend loads only: `backend/ml/models/EURUSD_gbdt_experimental.pkl`
- No model switching via environment variables
- No baseline/secondary model fallback
- If this file is missing, model loading fails by design

### Main Components

- `backend/app.py`: API server, auth, signal endpoints, background jobs
- `backend/ml/signal_generator_gbdt.py`: feature build + prediction engine
- `mobile_app/App.tsx`: app entry
- `mobile_app/src/screens/PredictionScreen.tsx`: live signal UI
- `mobile_app/src/screens/ProfileScreen.tsx`: in-app help/legal text modal
- `docs/`: privacy policy and terms

### Local Setup

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mobile:

```bash
cd mobile_app
npm install
npm start
```

### Quality Gate Commands (Local)

From repository root:

```bash
python tests/test_api_contract.py
python -m unittest discover -s tests -p "test_*.py" -v
```

From `mobile_app`:

```bash
npm test -- --watch=false
```

### Deployment Authority

- Current runtime authority: Fly.io (active environment).
- Azure deployment authority: manual release deployment only (`workflow_dispatch`).
- Azure deploy requires explicit confirmation text: `DEPLOY_AZURE`.

When preparing Azure release builds for mobile app, set:

```bash
EXPO_PUBLIC_API_BASE_URL=https://<your-azure-app>.azurewebsites.net
```

### Environment Notes

- `SAVE_CONFIDENCE_THRESHOLD` is optional and controls DB save threshold for auto-signals.
- `ALLOW_DEMO_AUTH_CODES=true` only for explicit local demo/debug use; default behavior does not expose codes.
- `ALLOW_TEST_NOTIFICATION_ENDPOINT=true` is required to use `/notifications/test` outside development mode.
- `AUTH_RATE_LIMIT_MAX_REQUESTS` and `AUTH_RATE_LIMIT_WINDOW_SECONDS` control auth endpoint abuse guardrails.
- `RATE_LIMIT_REDIS_URL` enables Redis-backed distributed rate limiting for multi-instance deployments.
- `TRUSTED_PROXY_COUNT` controls how many proxy hops are trusted for client IP resolution.
- `CORS_ALLOWED_ORIGINS` accepts comma-separated allowlist origins for production API access.
- `LOG_LEVEL` controls backend log verbosity (`INFO` default).
- `ALLOW_LOCAL_DOTENV=false` by default; production should use secret managers only.
- `JWT_ISSUER`, `JWT_AUDIENCE`, `ACCESS_TOKEN_EXPIRATION_MINUTES`, `REFRESH_TOKEN_EXPIRATION_DAYS` control access+refresh token lifecycle.
- Mobile auth + refresh tokens are stored in SecureStore with legacy AsyncStorage auto-migration for compatibility.
- Do not configure model-path/model-variant environment variables; they are not used.

### Disclaimer

This project is for research and educational use.
It does not provide financial advice.

---

## Монгол

Predictrix нь **EUR/USD хос дээр бодит цагийн сигнал гаргахад төвлөрсөн** mobile + backend систем бөгөөд ML runtime нь нэг тогтмол моделтой ажиллана.

### Одоогийн хувилбарын хүрээ

- Mobile апп: React Native (Expo), 0.4.5
- Backend: Flask API + MongoDB
- Сигнал модел: олон timeframe-тэй GBDT ensemble
- Runtime бодлого: **зөвхөн нэг идэвхтэй модел**

### Гол боломжууд

- EUR/USD хосын бодит цагийн арилжааны сигнал (BUY / SELL / HOLD) ба итгэлцлийн оноо
- Олон timeframe-тэй GBDT ensemble таамаглалын хөдөлгүүр
- Хэрэглэгчийн нэвтрэлт (JWT access + refresh token)
- Шинэ сигнал үүсэхэд push мэдэгдэл (Expo push)
- Автомат сигнал үүсгэх, зах зээлийн анализын background job-ууд
- Сигналын түүхийг MongoDB-д хадгалах
- Апп доторх тусламж ба эрх зүйн дэлгэц (нууцлалын бодлого, үйлчилгээний нөхцөл)

### Нэг моделийн runtime бодлого

- Backend зөвхөн энэ файлыг ачаална: `backend/ml/models/EURUSD_gbdt_experimental.pkl`
- Environment variable-аар модел солих боломжгүй
- Baseline эсвэл өөр fallback модел ашиглахгүй
- Энэ файл байхгүй бол зориуд алдаа өгч ачаалахгүй

### Гол бүрэлдэхүүн хэсгүүд

- `backend/app.py`: API сервер, нэвтрэлт, сигнал endpoint-ууд, background job-ууд
- `backend/ml/signal_generator_gbdt.py`: feature тооцоо + таамаглалын хөдөлгүүр
- `mobile_app/App.tsx`: аппын эхлэл
- `mobile_app/src/screens/PredictionScreen.tsx`: live сигналын дэлгэц
- `mobile_app/src/screens/ProfileScreen.tsx`: апп доторх тусламж/эрх зүйн текст
- `docs/`: нууцлал ба үйлчилгээний нөхцлийн баримт бичиг

### Local ажиллуулах

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mobile:

```bash
cd mobile_app
npm install
npm start
```

### Local чанарын шалгалтын командууд

Repository root-оос:

```bash
python tests/test_api_contract.py
python -m unittest discover -s tests -p "test_*.py" -v
```

`mobile_app` хавтсаас:

```bash
npm test -- --watch=false
```

### Deployment authority

- Одоогийн runtime authority: Fly.io (идэвхтэй орчин).
- Azure deployment authority: зөвхөн manual release deploy (`workflow_dispatch`).
- Azure deploy хийхдээ баталгаажуулах текст `DEPLOY_AZURE` оруулах шаардлагатай.

Azure release build бэлдэхдээ mobile app-д дараах хувьсагчийг өгнө:

```bash
EXPO_PUBLIC_API_BASE_URL=https://<your-azure-app>.azurewebsites.net
```

### Орчны тохиргооны тэмдэглэл

- `SAVE_CONFIDENCE_THRESHOLD` хувьсагч нь автоматаар хадгалах босгыг удирдана (сонголтот).
- `ALLOW_DEMO_AUTH_CODES=true` нь зөвхөн local demo/debug үед ашиглах зориулалттай; default горимд OTP/code буцаахгүй.
- `ALLOW_TEST_NOTIFICATION_ENDPOINT=true` байхгүй бол `/notifications/test` endpoint production горимд хаалттай байна.
- `AUTH_RATE_LIMIT_MAX_REQUESTS`, `AUTH_RATE_LIMIT_WINDOW_SECONDS` нь auth endpoint-ийн хамгаалалтын босгыг удирдана.
- `RATE_LIMIT_REDIS_URL` нь Redis дээр суурилсан distributed rate-limit-ийг идэвхжүүлнэ.
- `TRUSTED_PROXY_COUNT` нь client IP тодорхойлоход итгэх proxy hop-ийн тоог удирдана.
- `CORS_ALLOWED_ORIGINS` нь production-д зөвшөөрөх origin-уудыг comma-гаар өгнө.
- `LOG_LEVEL` нь backend логийн дэлгэрэнгүй түвшинг удирдана (`INFO` default).
- `ALLOW_LOCAL_DOTENV=false` нь default; production-д secret manager-only бодлого баримтална.
- `JWT_ISSUER`, `JWT_AUDIENCE`, `ACCESS_TOKEN_EXPIRATION_MINUTES`, `REFRESH_TOKEN_EXPIRATION_DAYS` нь access+refresh session lifecycle-г удирдана.
- Mobile auth + refresh token нь SecureStore-д хадгалагдаж, хуучин AsyncStorage token автоматаар migrate хийгдэнэ.
- Model path/model variant хувьсагчид ашиглагдахгүй.

### Анхааруулга

Энэ төсөл нь судалгаа, сургалтын зориулалттай.
Санхүүгийн зөвлөгөө өгөхгүй.
