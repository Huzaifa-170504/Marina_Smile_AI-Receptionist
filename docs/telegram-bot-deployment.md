# Run the Telegram bot with the FastAPI backend

The patient bot runs inside the existing FastAPI process. The Render web service
keeps its current Uvicorn start command; FastAPI starts Telegram polling during
startup when `TELEGRAM_BOT_TOKEN` is configured, and stops polling during
shutdown. No separate Render worker is needed.

## Run locally

1. Put the token in the project-root `.env` file as
   `TELEGRAM_BOT_TOKEN=...`. The application loads `.env`; it does not load a
   file named `my.env`. Never commit the real token.
2. Install the project requirements:

   ```powershell
   .\venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Start the full API and bot together:

   ```powershell
   .\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. In a private Telegram chat, send `/start`. The bot and API stop when you
   stop the local process.

## Patient booking and email lookup

Booking lookup accepts the patient's registered phone number or email and
requires their full name to match one active patient record. Telegram's
"Share registered phone" button remains available. Email lookup requires
applying `supabase/migrations/20261106104639_add_patient_email_for_telegram.sql`
to the project's database and adding the patient's email to their contact
record. This migration is additive; it is not applied automatically by the bot.

The bot checks active services, assigned doctors, and live slots. Receipt-
requiring requests remain pending until clinic staff verifies the payment.
On the patient's next **@ Booking status** check, the bot attempts to reserve
the selected slot and create/confirm its Google Calendar event. Services that
do not require payment are confirmed immediately through the same reservation
path. Confirmation requires the existing backend Supabase service key and
Google Calendar credentials to work.

Do not also run `python -m app.telegram.bot` while the API is polling with the
same token.

## Deploy to the existing Render web service

1. Review `git status --short` and `git diff -- render.yaml`. The Render file
   may contain your earlier local API configuration edits as well as the
   Telegram token setting.
2. Commit and push the code and Render configuration. Do not stage `.env`,
   `my.env`, or credential files.
3. In the existing `marina-smile-api` service in Render, add
   `TELEGRAM_BOT_TOKEN` under **Environment**. Blueprint `sync: false`
   entries added after initial Blueprint creation must be entered in the
   Dashboard.
4. Confirm that the API service already has `SUPABASE_SECRET_KEY`,
   `GEMINI_API_KEY`, and the Google service-account secret file. The bot uses
   those existing backend credentials.
5. Save the variable and deploy the latest commit to the existing web service.
6. Open the **web service** logs. Look for
   `Marina Smile Telegram polling started`. If no token is configured, the API
   logs that it is starting without Telegram.
7. Send `/start` to the bot, then try **@ See services** and the patient
   booking flow.

## Render free-plan limitation

The bot shares the API process, so there is no second Render service charge.
Render's free web service can sleep after 15 minutes without incoming HTTP
traffic, however. Telegram polling does not itself keep the web service awake;
the bot may not respond while it is asleep and may take time to resume after an
API request wakes it. This setup is suitable for integrated testing, not
guaranteed always-on operation.

Keep only one process polling with the token. Stop your local API while Render
is polling, or Telegram may report a polling conflict.

