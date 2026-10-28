# UtsavAI

UtsavAI creates culturally grounded event plans for Indian weddings, birthdays, and community celebrations. The app includes a multilingual planner, deterministic budget/timeline edits through the chat copilot, a plan critic, PDF/ICS/WhatsApp exports, and an offline wedding demo.

## Requirements

- Python 3.11 or newer
- Node.js 22 or newer and npm
- A Gemini API key for live plan generation and copilot requests
- A Firebase project with Google sign-in enabled
- SMTP credentials to send a plan email to the signed-in user

## Local development (Windows PowerShell)

Create and activate a virtual environment, then install the backend:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env.local
```

Set `GEMINI_API_KEY` in `.env.local`; never commit that file. `GEMINI_MODEL` may be set there to select an available model.
Temporary Gemini rate-limit and server-busy responses are retried up to three times with a short backoff; sustained provider outages still require retrying later.

## Configure Firebase sign-in and Google Calendar

1. In Firebase Console, create/select a project and add a **Web app**.
2. In **Authentication → Sign-in method**, enable Google. Add `localhost` to **Authentication → Settings → Authorized domains** for local testing.
3. Copy the web app's API key, Auth domain, project ID, and app ID into `.env.local` as `NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`, `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, and `NEXT_PUBLIC_FIREBASE_APP_ID`. Set `FIREBASE_PROJECT_ID` to the same project ID.
4. For local API token verification, configure Google Application Default Credentials with a Firebase Admin service account, or run `gcloud auth application-default login` as a project administrator. Set `GOOGLE_APPLICATION_CREDENTIALS` in `.env.local` to the service account JSON path if using a key file; keep that file outside Git.
5. In Google Cloud Console for the same project, enable the **Google Calendar API**. Configure the OAuth consent screen and add `https://www.googleapis.com/auth/calendar.events` as a requested scope. While the consent app is in testing, add the Google accounts you will use as test users.

Calendar permission is requested only when the user clicks **Add to Google Calendar**. It creates or updates an all-day event for the celebration and one event per timeline item, with a two-day popup reminder. The connected Google account must match the verified Firebase sign-in email.

## Configure plan email

The **Email me this plan** button sends the plan summary to the verified email on the Firebase account; the API does not accept a caller-supplied recipient address. Configure SMTP in `.env.local`, for example with a Gmail account that has 2-Step Verification and an app password:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-sender@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_FROM_EMAIL=your-sender@gmail.com
SMTP_USE_SSL=false
```

An SMTP provider such as Gmail, Resend, or SendGrid can be used. Do not commit the sender password or service account credentials.

Install the frontend dependencies:

```powershell
npm ci
```

Run the API and frontend in separate terminals:

```powershell
# Terminal 1
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

```powershell
# Terminal 2
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The Next.js development UI sends API requests to `http://localhost:8000`. Sign in with Google before using planner, copilot, email, calendar, or exports. The demo data itself does not need Gemini, but the app still requires a Firebase sign-in.

To open the development UI from another device on the same LAN, run Next.js with `npm run dev -- --hostname 0.0.0.0` and the API with `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`. Add the machine's LAN IP to `allowedDevOrigins` in `next.config.ts`, the exact frontend origin to CORS in `app/main.py`, and that IP under Firebase Authentication's authorized domains. The current config includes `192.168.1.7`; replace it if your machine's LAN IP changes.

## Tests and builds

```powershell
pytest -q
npm run build
```

`npm run build` emits a static Next.js site to `out/`; the FastAPI app serves that site from `app/static/` when it is present in the production image.

The optional live Gemini test makes a real model request and may incur usage charges:

```powershell
$env:UTSAVAI_LIVE_TEST = "1"
pytest -q tests/test_api.py::test_optional_live_gemini_integration
Remove-Item Env:UTSAVAI_LIVE_TEST
```

## API

- `GET /health`
- `GET /api/demo`
- Authenticated `POST /api/plan` — description, language (`en`, `hi`, `mr`), optional clarification answers
- Authenticated `POST /api/chat` — plan, message, language, and recent chat history
- Authenticated `POST /api/export/pdf`
- Authenticated `POST /api/export/ics`
- Authenticated `POST /api/export/whatsapp`
- Authenticated `POST /api/email/plan` — sends only to the verified Firebase account email
- Authenticated `POST /api/calendar/sync` — requires a Google OAuth access token with Calendar events permission

API endpoints are limited to 20 requests per minute per client IP. The in-memory plan cache is process-local and bounded; it is intended to avoid repeating identical demo/development requests, not to act as persistent storage.

## Docker

The production image builds the static Next.js frontend and serves it from a non-root Python 3.11 container. It includes Noto Sans Devanagari and HarfBuzz for Hindi/Marathi PDF export. Firebase web settings are public client configuration and must be passed as frontend build args:

```powershell
# Load the NEXT_PUBLIC_* values from your local .env.local into this shell.
Get-Content .env.local | ForEach-Object {
  if ($_ -match '^\s*(NEXT_PUBLIC_[^=]+)=(.*)$') {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process")
  }
}
docker build `
  --build-arg NEXT_PUBLIC_FIREBASE_API_KEY=$env:NEXT_PUBLIC_FIREBASE_API_KEY `
  --build-arg NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=$env:NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN `
  --build-arg NEXT_PUBLIC_FIREBASE_PROJECT_ID=$env:NEXT_PUBLIC_FIREBASE_PROJECT_ID `
  --build-arg NEXT_PUBLIC_FIREBASE_APP_ID=$env:NEXT_PUBLIC_FIREBASE_APP_ID `
  -t utsavai:local .
docker run --rm -p 8000:8080 --env-file .env.local -e PORT=8080 utsavai:local
```

Then open [http://localhost:8000](http://localhost:8000) and check [http://localhost:8000/health](http://localhost:8000/health).

## Deploy to Google Cloud Run (Windows PowerShell)

Cloud Build builds the container remotely, so Docker is not required on your Windows machine. Before starting:

- Link an active billing account to the Google Cloud project. The current project `utsavai-indian-event-planner` reports billing disabled, so Cloud Run deployment will fail until this is enabled in **Google Cloud Console → Billing**.
- Create a valid Gemini Developer API key. The key previously tested was rejected with 401; setting a different model cannot make an invalid key work.
- Revoke the SMTP app password that was exposed during local troubleshooting, then create a replacement. Store only the replacement in Secret Manager.
- In Firebase Console, confirm Google sign-in is enabled and note the Web app's API key, Auth domain, project ID, and app ID.

Authenticate with `gcloud auth login` and select the Firebase-linked Google Cloud project. The commands below use the project and region already selected in the development setup; change them if needed:

```powershell
$PROJECT_ID = "utsavai-indian-event-planner"
$REGION = "asia-south1"
$REPOSITORY = "utsavai"
$SERVICE = "utsavai"
$IMAGE = "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${SERVICE}:latest"
$RUNTIME_SA_NAME = "${SERVICE}-runtime"
$RUNTIME_SA = "${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Copy the four public Web app settings from Firebase Console > Project settings.
$NEXT_PUBLIC_FIREBASE_API_KEY = "your-firebase-web-api-key"
$NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN = "your-project.firebaseapp.com"
$NEXT_PUBLIC_FIREBASE_APP_ID = "your-firebase-web-app-id"

gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com `
  secretmanager.googleapis.com cloudbuild.googleapis.com identitytoolkit.googleapis.com `
  calendar-json.googleapis.com

# These create commands are for first-time setup only.
gcloud artifacts repositories create $REPOSITORY `
  --repository-format=docker --location=$REGION `
  --description="UtsavAI container images"
gcloud iam service-accounts create $RUNTIME_SA_NAME `
  --display-name="UtsavAI Cloud Run runtime"

# Create gemini-api-key and smtp-password in Google Cloud Console >
# Security > Secret Manager. Add the new Gemini key and rotated SMTP password
# as secret versions there; do not paste secret values into build commands.
gcloud secrets add-iam-policy-binding gemini-api-key `
  --member="serviceAccount:${RUNTIME_SA}" `
  --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding smtp-password `
  --member="serviceAccount:${RUNTIME_SA}" `
  --role="roles/secretmanager.secretAccessor"

# Token revocation checks require read access to Firebase Auth users.
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:${RUNTIME_SA}" `
  --role="roles/firebaseauth.viewer"

$SUBSTITUTIONS = "_IMAGE=$IMAGE,_FIREBASE_API_KEY=$NEXT_PUBLIC_FIREBASE_API_KEY,_FIREBASE_AUTH_DOMAIN=$NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,_FIREBASE_PROJECT_ID=$PROJECT_ID,_FIREBASE_APP_ID=$NEXT_PUBLIC_FIREBASE_APP_ID"
gcloud builds submit --config=cloudbuild.yaml --substitutions=$SUBSTITUTIONS .

$SMTP_USERNAME = Read-Host "SMTP sender email address"
gcloud run deploy $SERVICE `
  --image=$IMAGE `
  --region=$REGION `
  --service-account=$RUNTIME_SA `
  --allow-unauthenticated `
  --timeout=600 `
  --max=5 `
  --set-secrets="GEMINI_API_KEY=gemini-api-key:latest,SMTP_PASSWORD=smtp-password:latest" `
  --set-env-vars="GEMINI_MODEL=gemini-2.5-flash,FIREBASE_PROJECT_ID=$PROJECT_ID,SMTP_HOST=smtp.gmail.com,SMTP_PORT=587,SMTP_USERNAME=$SMTP_USERNAME,SMTP_FROM_EMAIL=$SMTP_USERNAME,SMTP_USE_SSL=false"

$SERVICE_URL = gcloud run services describe $SERVICE `
  --region=$REGION --format="value(status.url)"
$SERVICE_URL
Invoke-RestMethod "$SERVICE_URL/health"
```

The Firebase Web app settings are public client configuration; Gemini and SMTP credentials must stay in Secret Manager. If using a non-Gmail SMTP provider, replace the SMTP host, port, sender, and SSL settings with that provider's values.

After deployment:

1. Add the service hostname (without `https://`) from `$SERVICE_URL` to **Firebase Console → Authentication → Settings → Authorized domains**.
2. In Google Cloud Console, confirm the Google OAuth consent screen includes the `https://www.googleapis.com/auth/calendar.events` scope. If the consent screen is in testing, add each person testing Calendar sync as a test user. Google Calendar API must be enabled in the same project.
3. Open `$SERVICE_URL`, sign in, and test plan generation, plan email, and Calendar sync. `/health` confirms the app is running and whether a Gemini key is configured; it does not confirm that the key is valid.
