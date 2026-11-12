# NBA Fantasy Analytics — GitHub and Streamlit Cloud

## Local version
Keep your existing local folder and `nba_analytics.sqlite3` unchanged. You can copy the updated Python files into that folder if desired, but **back up the folder first**. The local launcher still uses SQLite because it clears `DATABASE_URL`. Your `.venv` folder does **not** have to be beside `app.py`: it can live elsewhere if you activate it or change the launcher to point to its Python executable. The current launcher expects `.venv\Scripts\python.exe` beside `app.py`, and otherwise falls back to `python` on PATH.

## GitHub
1. Create a new **private** GitHub repository (for example `nba-fantasy-analytics`).
2. Upload the contents of this ZIP **at the repository root**, not inside an extra folder. GitHub must show `app.py` and `requirements.txt` at the top level.
3. Do **not** upload `.venv`, `nba_analytics.sqlite3`, `.streamlit/secrets.toml`, `.env`, the local `.bat` launcher, or `__pycache__`.
4. If secrets were ever uploaded to GitHub, remove them from history and rotate credentials; `.gitignore` only prevents future tracked additions.

## Persistent hosted database
1. Create a PostgreSQL database with a free tier provider such as Neon. Obtain its PostgreSQL connection string, preferably one requiring TLS (`sslmode=require`).
2. Streamlit Community Cloud > Create app > select repository and branch > set entrypoint to `app.py`.
3. In the app's **Advanced settings / Secrets**, add the TOML keys below, using your actual values. Do not upload a secrets file to GitHub:

```toml
DATABASE_URL = "postgresql://USERNAME:PASSWORD@HOST/DBNAME?sslmode=require"
ESPN_LEAGUE_ID = "YOUR_LEAGUE_ID"
ESPN_S2 = "YOUR_ESPN_S2_COOKIE"
SWID = "YOUR_SWID_COOKIE"
```

**Verify the actual key names** in your local `.streamlit/secrets.toml` and the app's settings; the app code reads ESPN_LEAGUE_ID, ESPN_S2, SWID. The online app and local app will use different databases by default.

4. Choose a compatible Python runtime (Python 3.12 is a reasonable starting point). Deploy and check logs.
5. **Initial cloud database starts empty.** This ZIP does not migrate your local historical NBA data. The cloud app's manual Refresh can download data, but NBA API requests from hosted servers may be blocked or time out. A separate migration of your local SQLite data to PostgreSQL is preferable if you want history immediately.
6. Keep in mind: a **private GitHub repository does not necessarily make the deployed Streamlit app private**. Check Community Cloud's sharing/access controls before entering ESPN cookies or displaying private league data. If adequate private access is unavailable, use an authentication gate or different hosting.

## Verification / limitations
All bundled `.py` files passed Python syntax compilation. A real Neon connection, ESPN access, NBA API refresh and deployment have **not** been tested. This is a deployable starting package, not a guarantee that the cloud refresh succeeds.

## Cleanup
- Local: retain all `.py` modules, `requirements.txt`, `nba_analytics.sqlite3`, launcher, rankings seed. `.venv` is optional if Python dependencies are installed elsewhere. Delete `__pycache__` freely; the rankings cache can be regenerated. `README.txt` optional.
- GitHub: keep files in this ZIP. Never include local database, secrets, venv, cache or launcher.
