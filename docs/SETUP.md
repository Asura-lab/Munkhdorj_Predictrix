# Setup & Reproduction Guide

How to set up, run, rebuild, and reproduce **Predictrix** from scratch — the
Flask backend, the React Native (Expo) mobile app, and the ML training / backtest
pipeline.

For the full list of environment variables and secrets, see
**[CONFIGURATION.md](CONFIGURATION.md)**.

---

## 1. Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Python | 3.11 (see `backend/runtime.txt`) | backend API + ML pipeline |
| Node.js | 20 LTS | mobile app (Expo) |
| MongoDB | 6.x+ local, or MongoDB Atlas | data store |
| Git | any | clone (+ submodule for the thesis) |
| EAS CLI | latest (`npm i -g eas-cli`) | mobile release builds (optional) |
| Redis | optional | distributed rate limiting in production |
| MetaTrader 5 | optional | MT5 backtest of generated signals |

---

## 2. Clone the repository

The thesis lives in `diplom/`, a Git **submodule** that points to
`Asura-lab/diplom`. Clone recursively so the thesis source comes with it:

```bash
git clone --recursive https://github.com/Asura-lab/Forex-Signal-App.git
# already cloned without --recursive?
git submodule update --init --recursive
```

---

## 3. Backend — Flask API

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows PowerShell
# source .venv/bin/activate           # macOS / Linux
pip install -r requirements.txt

Copy-Item config\.env.example config\.env   # then edit config\.env
```

Minimum to run locally (everything else has safe defaults — see CONFIGURATION.md):

- `MONGO_URI` — leave it unset to use the local fallback
  `mongodb://localhost:27017/users_db` (needs a local MongoDB), or paste a
  MongoDB Atlas URI.
- `SECRET_KEY` — any random string for local dev.

Run the API (auto-loads `config/.env` in dev):

```powershell
.\start-local.ps1     # sets dev CORS, activates venv, runs app.py
# or:
python app.py         # http://localhost:5000
```

Background jobs (the signal scheduler) run **in-process** by default
(`APP_PROCESS_ROLE=all`). To split API and worker the way production does
(see `docs/architecture/ADR-001-separate-api-and-background-workers.md`):

```bash
APP_PROCESS_ROLE=api     python app.py     # API only   (or backend/start-api.sh)
APP_PROCESS_ROLE=worker  python worker.py  # worker only (or backend/start-worker.sh)
```

Health check: `GET http://localhost:5000/health`.

---

## 4. Mobile app — React Native / Expo

```powershell
cd mobile_app
npm install
Copy-Item .env.example .env    # optional: point the app at your backend
```

Development:

```powershell
npm start          # Expo dev server — press a / i / w for Android / iOS / web
npm run android    # expo run:android (native build + run)
```

- Android **emulator** reaches a local backend at `http://10.0.2.2:5000`
  automatically.
- **Physical device:** set `EXPO_PUBLIC_LOCAL_API_HOST` in `.env` to your PC's
  LAN IP (e.g. `192.168.1.50`).

Release builds (EAS profiles are defined in `eas.json`):

```bash
npm i -g eas-cli
eas login
eas build -p android --profile preview       # internal APK
eas build -p android --profile production     # Play Store AAB
```

> `owner` and `extra.eas.projectId` in `app.json` belong to the original Expo
> account (`asura08`). If you fork the project, change them to **your own** Expo
> account, or build locally with Gradle:
> `cd android && ./gradlew assembleRelease`.

Release against the Azure backend instead of the default Fly.io URL:

```bash
EXPO_PUBLIC_API_BASE_URL=https://<your-app>.azurewebsites.net \
  eas build -p android --profile production
```

---

## 5. ML model — retrain & reproduce

The research / training pipeline is in **`model & backtest result/`**.

```bash
cd "model & backtest result"
pip install -r code/requirements.txt

python code/build_from_train.py               # build dataset (CSV -> pickle)
python code/train_models.py --symbol EURUSD   # train LightGBM/XGBoost/CatBoost ensemble
python code/generate_signals_2025.py          # produce trading signals (MT5 CSV)
```

Reproducibility knobs (environment variables):

- `MULTI_SEED_COUNT` — number of training seeds (default `3`).
- `WF_EMBARGO_MINUTES` — walk-forward embargo window (default `60`).
- `GIT_COMMIT` — stamped into the model manifest for traceability.

> **Training data is not committed** — the raw EUR/USD CSVs are large and
> `.gitignore`d. Put your OHLC history in the data directory expected by
> `build_from_train.py` before retraining. The model the backend actually serves
> is committed at `backend/ml/models/EURUSD_gbdt_experimental.pkl`.

MT5 backtest: copy `model & backtest result/results/signals_2025.csv` into the
MT5 `Common\Files\` folder and run the EA at
`mt5/experts/ForexSignalBacktestEA.mq5`.

---

## 6. Tests & quality gates

```bash
# Backend (from repo root)
python tests/test_api_contract.py
python -m unittest discover -s tests -p "test_*.py" -v
python tests/deterministic_rerun_check.py

# Mobile (from mobile_app)
npm test -- --watch=false
```

CI runs the same gates on every push / PR via
`.github/workflows/azure-deploy.yml` (ruff lint, contract tests, dependency
audit, gitleaks secret scan).

---

## 7. Deployment (overview)

- **Fly.io** — the active runtime. Config `backend/fly.toml`; helper
  `backend/deploy_fly.ps1`. Set secrets with `fly secrets set KEY=value`.
- **Azure App Service** — manual release only: GitHub Actions →
  *Quality Gates and Azure Deploy* → `workflow_dispatch`, with
  `confirm_deploy = DEPLOY_AZURE`. Requires repo secret
  `AZURE_WEBAPP_PUBLISH_PROFILE` (optional var `AZURE_APP_URL`).
- **Never commit real secrets.** Use the platform secret manager. Rotation steps:
  `docs/SECRET_ROTATION_REVOKE_RUNBOOK.md`.

---

## 8. Project layout

```
backend/                 Flask API, auth, signal endpoints, scheduler, ML serving
  config/                settings + .env(.example)
  ml/                    model contract + GBDT signal generator + served model
  utils/                 data-source handlers, market analysis, push notifications
  app.py / worker.py     API entrypoint / background worker
mobile_app/              React Native (Expo) app
  src/                   screens, components, config (api.ts)
  android/               native Android project
model & backtest result/ ML training + backtest pipeline, figures, report
mt5/                     MetaTrader 5 Expert Advisor (backtest)
tests/                   backend contract / security / durability tests
docs/                    architecture (ADRs), policies, runbooks, this guide
diplom/                  thesis LaTeX source (Git submodule -> Asura-lab/diplom)
```
