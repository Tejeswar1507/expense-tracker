# Morning setup checklist (about 30 minutes, all free)

Everything is built and tested. These steps need **your** sign-ins, so only you can do them.
Do them in order; each says what you'll copy for later. Keep a notepad open for the values marked 📋.

> **First, one important finding.** Your Gmail (teja20.sunkara@gmail.com) has **no bank or UPI alert emails** in the last
> few months — only Razorpay receipts. Email capture needs them. In each bank's app or net banking, turn on
> **email alerts for every debit/credit (including UPI)** to this Gmail. Your wife does the same to her Gmail.
> (If a bank only sends SMS, tell me which one; there's an SMS route.)

---

## 1. GitHub repo ✅ done
The code is in your private repo **github.com/Tejeswar1507/expense-tracker** (branch `main`).

## 2. Database: Neon (3 min)
1. Go to **neon.tech** → Sign up with Google.
2. Create project: name `expense-tracker`, region **AWS Asia Pacific (Singapore)**, Postgres 16.
3. On your project's **Dashboard** page, click the **Connect** button (top right). A "Connect to your database" window opens.
   Leave the defaults (branch `production`/`main`, database `neondb`, role `neondb_owner`) and keep **Connection pooling** on.
   You'll see a line starting with `postgresql://neondb_owner:` — that is the **connection string**. Click the **copy** icon
   next to it (the password is hidden on screen, but the copy button copies the full string including the password).
4. 📋 Paste it into your notepad and label it `DATABASE_URL`.
   `DATABASE_URL` is not something you'll see in Neon — it's just the name this app uses for the connection string.
   You'll paste the same string in two places later: Streamlit secrets (step 5) and GitHub secrets (step 6).
   It looks like: `postgresql://neondb_owner:AbC123...@ep-cool-name-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
   Keep it private: anyone with it can read your data.

## 3. Google access for Gmail + Drive (10 min)
1. **console.cloud.google.com** → top bar → New project → `expense-tracker`.
2. APIs & Services → **Library** → enable **Gmail API** and **Google Drive API**.
3. **Set up the consent screen** (the "Allow Expense Tracker to…" page you'll see when you sign in).
   Google now calls this section **Google Auth Platform**. It has many pages (Overview, Branding, Audience,
   Clients, Data Access, Verification Center…). **You only need three of them; leave everything else as it is.**

   **3a. Create it**
   - Left menu ☰ → **APIs & Services** → **OAuth consent screen** (it may open as "Google Auth Platform").
   - If it says *"Google Auth Platform not configured yet"*, click **Get started** and fill the 4 short screens:

     | Screen | What to enter |
     |---|---|
     | App Information | App name: `Expense Tracker` · User support email: your Gmail → **Next** |
     | Audience | Choose **External** → **Next** |
     | Contact Information | Your Gmail → **Next** |
     | Finish | Tick *I agree to the Google API Services: User Data Policy* → **Continue** → **Create** |

   - Skip logo, app domain, homepage and privacy links — they're optional for a personal app.

   **3b. Data Access → add the 3 permissions**
   - Left menu of Google Auth Platform → **Data Access** → **Add or remove scopes**.
   - A side panel opens with a long list. Don't scroll through it — at the bottom, under
     **Manually add scopes**, paste this whole line and click **Add to table**:
     `https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/drive.file`
   - Click **Update**, then **Save** at the bottom of the Data Access page.
   - Google labels the Gmail ones "sensitive/restricted". That's normal and fine for personal use; you do **not**
     need to submit anything in Verification Center.

   **3c. Audience → test user, then publish**
   - Left menu → **Audience** → under **Test users** → **Add users** → `teja20.sunkara@gmail.com` → **Save**.
   - On the same Audience page, under **Publishing status** ("Testing"), click **Publish app** → **Confirm**.
     Status changes to **In production**.

   Why publish? In "Testing", Google cancels the app's Gmail access every 7 days, so the hourly job would stop
   weekly. "In production" without verification is allowed for personal apps (up to 100 users); the only side
   effect is a warning when you sign in in step 5: **"Google hasn't verified this app"** → click **Advanced** →
   **Go to Expense Tracker (unsafe)**. It says "unsafe" only because Google hasn't reviewed it; it's your own app.

4. APIs & Services → **Credentials** → Create credentials → OAuth client ID → **Web application**:
   - Name: `Expense Tracker`
   - Authorized redirect URIs: `https://developers.google.com/oauthplayground` and `http://localhost:8501/oauth2callback`
     (you'll add the Streamlit address in step 5)
   - 📋 `GOOGLE_CLIENT_ID`, 📋 `GOOGLE_CLIENT_SECRET`
5. Get the refresh token in the browser (no installs):
   1. Open **developers.google.com/oauthplayground** → gear icon ⚙ → tick **Use your own OAuth credentials** → paste the client ID and secret.
   2. In "Input your own scopes" paste:
      `https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/drive.file`
   3. **Authorize APIs** → sign in as teja20.sunkara@gmail.com → Advanced → Go to Expense Tracker → Allow.
   4. **Exchange authorization code for tokens** → 📋 `GOOGLE_REFRESH_TOKEN` (the long "Refresh token" value).
   *(Alternative if you prefer Python: `python scripts/get_google_token.py client_secret.json`.)*

## 4. Gmail filters (5 min)
**Your Gmail:** create a label **`Bank-Alerts`**. Create a filter: From = your banks' alert addresses and UPI apps
(e.g. `alerts@hdfcbank.net OR alerts@axisbank.com OR ...` — copy them from a real alert) → *Apply label Bank-Alerts*.
Tick "Also apply to matching conversations".

**Your wife's Gmail (on her phone or laptop):**
1. Settings → See all settings → **Forwarding and POP/IMAP** → Add a forwarding address → `teja20.sunkara@gmail.com`.
   Gmail emails you a confirmation code; give it to her.
2. Create a filter: From = her banks' alert addresses → **Forward to teja20.sunkara@gmail.com**.
Your Bank-Alerts filter should also match these forwarded emails (same sender addresses).

## 5. Web app: Streamlit Community Cloud (5 min)
1. **share.streamlit.io** → Continue with GitHub → **Create app** → Deploy from GitHub:
   repo `Tejeswar1507/expense-tracker`, branch `main`, main file **`app/main.py`**,
   App URL e.g. `teja-expenses` → 📋 `APP_URL` = `https://teja-expenses.streamlit.app`.
2. Advanced settings → Python **3.12** → **Secrets**: paste `.streamlit/secrets.example.toml` filled with your values.
   For `[auth]`: `redirect_uri = "https://teja-expenses.streamlit.app/oauth2callback"`, same client ID/secret as step 3,
   and `cookie_secret` = any long random text.
3. Back in Google Cloud → Credentials → your OAuth client → add redirect URI
   `https://teja-expenses.streamlit.app/oauth2callback` → Save.
4. Deploy. Open the URL → **Sign in with Google**. (Optional extra lock: app Settings → Sharing → "Only specific people",
   add both emails.)

## 6. GitHub secrets for the hourly job (3 min)
Repo → Settings → Secrets and variables → **Actions** → New repository secret, one each:
`DATABASE_URL`, `ADMIN_EMAIL` (teja20.sunkara@gmail.com), `MEMBER_EMAIL` (your wife's Gmail),
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `APP_URL`.
(`ANTHROPIC_API_KEY` only if you switch on AI later.)
Then **Actions** tab → enable workflows → **Hourly job** → *Run workflow* once to test.

## 7. In the app (5 min) — Admin screen
1. **Import 2026 sheet:** in Google Sheets open "2026 Expense Tracker" → File → Download → .xlsx → upload it.
   You'll see every month checked against the sheet (all 10 months matched in my test).
2. **Accounts:** add each bank account and card (both of you): bank, last 4 digits, and your own UPI IDs
   (e.g. `tejeswarsunkara@ybl`). Add your names as banks print them (e.g. `Tejeswar Sunkara, Babitha`) — this is how
   transfers between you are recognised and not counted twice.
3. Look at **Budgets & categories** (imported from your sheet) and adjust.

## 8. Your wife
Send her the app link; she signs in with Google and taps *Add to Home Screen*. She'll get a 9 PM email only when
something needs her input.

---

### If something goes wrong
- **No transactions arriving:** Admin → Emails. "failed" emails show the text; send me one (mask amounts) and I'll add
  its wording to the parser, then press *Re-read failed emails*.
- **"Not allowed" on sign-in:** the email isn't ADMIN_EMAIL / MEMBER_EMAIL in Streamlit secrets.
- **Hourly job red in GitHub:** open the run log; most often a secret is missing or mistyped.
