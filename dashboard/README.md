# Marina Smile Operations Dashboard

Role-aware clinic operations UI built with Next.js 16, React 19, TypeScript,
Tailwind CSS, Supabase Auth, and Supabase Realtime.

## Local setup

From the repository root:

```powershell
Copy-Item dashboard/.env.example dashboard/.env.local
cd dashboard
npm.cmd install
npm.cmd run dev
```

Fill `dashboard/.env.local` with values from the **new** Supabase project:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://your-new-project.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=your-publishable-key
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

# Server-only recruiter demo proxy values:
DEMO_BACKEND_URL=http://localhost:8000
DEMO_API_TOKEN=replace-with-the-backend-demo-token
DEMO_FINGERPRINT_SECRET=replace-with-a-separate-random-secret
```

`NEXT_PUBLIC_SUPABASE_ANON_KEY` is supported only as a compatibility fallback.
Never put a service-role/secret key, database password, Gemini key, Google
private key, WhatsApp token, or Meta app secret in a `NEXT_PUBLIC_*` variable.
The demo proxy values are server-only and must not use the `NEXT_PUBLIC_`
prefix.

The public `/` route is the recruiter-facing project experience. Its live demo
calls same-origin Next.js route handlers, which add a one-way visitor HMAC and
forward the request to FastAPI using a server-only token. `/login` and all
`/dashboard` routes retain the staff authentication boundary.

Run the FastAPI service separately from the repository root:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

The backend `.env` must include:

```dotenv
DASHBOARD_ALLOWED_ORIGINS=http://localhost:3000
DASHBOARD_APPLICATION_URL=http://localhost:3000
```

`DASHBOARD_APPLICATION_URL` must be a trusted origin without a path. The
backend uses it only as the destination for Auth emails.

## Authentication and authorization

Supabase Auth establishes identity. The application then resolves the matching
active `staff_users` row to determine clinic, role, doctor relationship, and
explicit permission overrides. There is no public registration flow. Owners
create staff through protected FastAPI operations. Password setup, recovery,
and logged-in changes write credentials only to Supabase Auth.

The browser and server use request-scoped Supabase SSR clients and cookie-based
sessions. `src/proxy.ts` refreshes sessions on protected routes. The dashboard
shell performs the active-staff check again on the server, and the FastAPI API
validates each bearer token independently before protected mutations.

RLS remains authoritative for direct reads. Critical changes use protected
FastAPI endpoints backed by existing database RPCs or carefully scoped trusted
server writes.

## Implemented areas

- Owner: today's overview, appointments, queue, booking requests, patients,
  doctors, services, schedules/overrides, payments, staff/permissions,
  handoffs, audit, and read-only clinic settings.
- Doctor: own queue, calendar, appointments, assigned patients, and availability
  overrides.
- Receptionist: operational appointments, queue, booking requests, patients,
  doctors, schedule, and handoffs.
- Payment verifier: pending-payment overview, private proof viewing, approval,
  and rejection.
- Realtime refreshes for appointments, visits, payments, handoffs, dentists,
  weekly schedules, and schedule overrides where used by a page.

Booking, rescheduling, and cancellation are intentionally read-only/backend-
pending. The current Python services still target the previous database
contract and are not exposed through this dashboard.

## Quality checks

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

## Hosted Supabase Auth configuration

These settings are manual and are not modified by the application:

1. In **Authentication > URL Configuration**, use `http://localhost:3000` as
   the Site URL for local development. Change it to the public HTTPS dashboard
   origin for production.
2. Add `http://localhost:3000` to Redirect URLs. Later add the exact production
   origin, for example `https://dashboard.example.com`.
3. In the **Invite user** email template, use:

   ```html
   <a href="{{ .RedirectTo }}/auth/confirm?token_hash={{ .TokenHash }}&type=invite">
     Set up your Marina Smile account
   </a>
   ```

4. In the **Reset password** email template, use:

   ```html
   <a href="{{ .RedirectTo }}/auth/confirm?token_hash={{ .TokenHash }}&type=recovery">
     Reset your Marina Smile password
   </a>
   ```

5. Review **Email OTP Expiration**. One hour is appropriate unless clinic
   policy requires a shorter period.
6. Supabase's default mailer can support limited local testing but is rate
   limited. Configure custom SMTP before production and disable link tracking.
   Some newer Free projects require custom SMTP before templates can be edited.

The confirmation route accepts only `invite` and `recovery`, maps them to fixed
local pages, verifies the one-time TokenHash, and removes Auth parameters from
the redirected URL.

## Password and email recovery policy

Employees who know their registered email can reset their password without
Owner involvement. The public application deliberately cannot discover or
reveal staff email addresses. Someone who has forgotten the registered email
must use a trusted clinic support process. Authenticated email change is not
part of this milestone.

## Manual login check

1. Sign in as an Owner and add a staff member or doctor through the dashboard.
2. Open the invitation email and set a password. Confirm access remains blocked
   when the separate staff access status is inactive.
3. Start FastAPI on port 8000 and the dashboard on port 3000.
4. Open `http://localhost:3000/login`, sign in, and confirm the navigation and
   records match the staff role and clinic.
5. Sign out and confirm `/dashboard` redirects to `/login`.
