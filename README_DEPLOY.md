# MandiGuard Deployment Guide (Render)

### Deploy Steps
1. Push repository to GitHub.
2. In Render Dashboard, click **New +** -> **Blueprint**, and select this repo (`render.yaml`).
3. Enter the un-synced secrets (`DATAGOV_KEY`, `TG_TOKEN`, `TG_CHAT_ID`) in the prompt.
4. Click **Apply**. `mandiguard-api` and `mandiguard-ui` will build and launch automatically.

### Key Environment Variables
| Variable | Service | Purpose |
|---|---|---|
| `DATAGOV_KEY` | API | Data.gov.in Agmarknet API key (secret, `sync: false`) |
| `TG_TOKEN` | API | Telegram bot token (secret, `sync: false`) |
| `TG_CHAT_ID` | API | Authorized Telegram chat ID (secret, `sync: false`) |
| `DB_PATH` | API | Persistent SQLite path (`/var/data/mandiguard.db` on mounted disk) |
| `MG_API_URL` | UI | Public API URL (`https://mandiguard-api.onrender.com`) |
| `MG_CORS_ORIGINS` | API | Allowed frontend origins (comma-separated) |
| `MG_MODE` | Both | Set to `live` for live dispatch operations |
| `MG_WATCHDOG` / `MG_TELEGRAM` | API | Set `1` to run in-process monitoring & Telegram polling |
| `MG_DEMO` | API | Set `1` for demo polling speed (15s watchdog ticks) |

### Key Rotation & Restarts
- **Rotate Keys**: Update `DATAGOV_KEY` or `TG_TOKEN` in Render dashboard -> Environment.
- **Restart**: Click **Manual Deploy** -> **Restart service** on either service.

### Known Limits & Demo Checklist
- **One instance only**: Worker count must be 1 (`--workers 1`) to avoid duplicate watchdog ticks.
- **Free tier idle sleep**: Free services sleep on idle; ping `/health` 2 min before your demo.
- **Request timeout**: Render proxy timeout is 100s; live `POST /plan` takes ~40s (safe).
