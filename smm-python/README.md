# SMM Python Panel (Flask)

A separate Python-based SMM panel inspired by your current PHP SMM script.

## Features
- User registration/login
- Services list
- Place new orders
- Orders history
- Support tickets
- Admin panel:
  - Dashboard
  - Manage services
  - Import/sync services from multiple providers
  - Forward orders to provider APIs automatically
  - Sync provider order status
  - Provider refill/cancel actions
  - Manage orders
  - Manage tickets
  - Manage user balances

## Folder
`c:\Users\HUAWEI\OneDrive\Desktop\smm\smm-python`

## Setup (Windows PowerShell)
```powershell
cd "c:\Users\HUAWEI\OneDrive\Desktop\smm\smm-python"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:FLASK_APP = "run.py"
flask init-db
python run.py
```

Open: `http://127.0.0.1:5000`

## Production env vars
- `SECRET_KEY` (required in production)
- `SQLALCHEMY_DATABASE_URI` or `DATABASE_URL`
- `SMM_ADMIN_USERNAME`, `SMM_ADMIN_EMAIL`, `SMM_ADMIN_PASSWORD` (used only when no admin exists yet)
- See `.env.example` for full list.

## Deploy (Render)
1. Push this folder to GitHub.
2. Create a Render Web Service from the repo.
3. Use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn run:app --bind 0.0.0.0:$PORT --workers 2`
4. Set env vars:
   - `SECRET_KEY`
   - `SQLALCHEMY_DATABASE_URI` (or `DATABASE_URL`)
5. If using SQLite on Render, attach a persistent disk and point DB URI to that disk path.

## Deploy (VPS)
Run with Gunicorn behind Nginx:
```bash
gunicorn run:app --bind 127.0.0.1:8000 --workers 2
```
Then reverse-proxy with Nginx to `127.0.0.1:8000`.

## Initial admin
- If no admin exists yet, one is auto-created at startup.
- You can set these before first run:
  - `SMM_ADMIN_USERNAME`
  - `SMM_ADMIN_EMAIL`
  - `SMM_ADMIN_PASSWORD`
- Default fallback password is `ChangeMe@123`; change it immediately.

## Notes
- Default SQLite path is in Flask instance folder (`instance/smm_python.db`).
- This project is fully separate from your PHP SMM files.
