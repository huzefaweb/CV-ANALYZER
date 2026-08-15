# CV-ANALYZER

CV Analyzer is a private recruiter tool that compares multiple candidate Resumes against a single Job Description, ranks candidates with a traceable Evidence trail, and generates grounded interview questions for shortlisted candidates. Scores and rankings are decision *support* — the Recruiter always makes the final call, never the tool.

It runs as four containers on one Docker Compose host: a Next.js web app, a FastAPI gateway, a Python analysis worker, and PostgreSQL. Everything needed to run it locally is in this repo — no cloud account is required to try it out.

## Prerequisites

- Docker and Docker Compose. Nothing else needs to be installed locally — Node, Python, and PostgreSQL all run inside containers.
- ~2 GB free disk for the local AI model (pulled once, see below).
- A modern Chrome-based browser (used for the app itself and for printing Candidate Reports).

## First-time setup

**1. Configure environment variables.**

```sh
cp .env.example .env
```

Open `.env` and fill in:

```env
POSTGRES_USER=cvanalyzer
POSTGRES_PASSWORD=<pick a password>
POSTGRES_DB=cvanalyzer
```

Leave every other line in `.env` blank for now — `AUTH0_*` and `AZURE_OPENAI_*` are optional integrations (see "Using Azure OpenAI instead of the local model" below). With them blank, the app uses its built-in local-account login and a local AI model — nothing external to sign up for.

**2. Start the database and pull the local AI model** (one-time; the model is downloaded once and reused on every future start):

```sh
docker compose up -d postgres ollama
docker compose exec ollama ollama pull qwen2.5:0.5b-instruct
```

This pulls a small (~400 MB) local model that runs entirely on your machine — no API key, no external calls, no cost. It's deliberately lightweight, so expect analysis steps to take tens of seconds rather than being instant (see "Known limitations" below).

**3. Start everything else:**

```sh
docker compose up -d --build gateway worker web
```

`gateway` applies database migrations automatically on boot. `web` runs a production build, which takes about 15–30 seconds the first time. Watch for it to finish:

```sh
docker compose logs web --tail 20
```

Wait for a line like `✓ Ready in <N>ms` before opening the app. Then confirm everything is up:

```sh
docker compose ps
```

`postgres` and `ollama` should show `healthy`; `gateway`, `worker`, and `web` will just show `Up` (they have no built-in healthcheck — that's expected, not a problem, as long as `web`'s log showed "Ready").

**4. Open the app and create your account.**

Go to **http://localhost:3000** — you'll land on the sign-in page. Click "Create one" to register with an email and password.

**5. Admit your account.**

New accounts aren't usable until admitted — this is intentional (no self-service admission, no public admin page, matching how a private recruiter tool should gate access). Run this once per account, substituting the email you just registered with and the `POSTGRES_USER`/`POSTGRES_DB` values from your `.env`:

```sh
docker compose exec postgres psql -U cvanalyzer -d cvanalyzer -c "UPDATE users SET admitted_at = now() WHERE email = 'you@example.com';"
```

Now sign in at http://localhost:3000/login — you'll land on your workspace, ready to start an analysis.

## Everyday start / stop

Once step 2 above (the model pull) has been done once on a machine, you don't need to repeat it. To start the app again later:

```sh
docker compose up -d
```

To stop it (this keeps your data — Postgres data and the downloaded model both persist in named volumes):

```sh
docker compose down
```

If a single service misbehaves, restart just that one rather than the whole stack:

```sh
docker compose restart web   # or gateway / worker
```

If you edit any application code and want the change picked up, rebuild that service:

```sh
docker compose up -d --build web   # or gateway / worker
```

## Using the app

1. **New Analysis** (`/new-analysis`) — paste in a Job Description (minimum 200 characters of real content) and upload 1–20 candidate Resumes (PDF or DOCX, 10 MB max each). Click **Analyze** once at least one Resume is uploaded and the Job Description is valid — this freezes both as one comparison set.
2. **Analysis Progress** — you're taken to a live progress view. This step calls the local AI model once to derive Job Requirements from the Job Description, then once per Resume to evaluate it — expect roughly 20–40 seconds per Resume with the local model. The page updates on its own; no need to refresh.
3. **Results** — once every Resume reaches a final outcome, click **View Results** to see Ranked, Needs Review, and Failed candidates, each with a rounded score and a short Evidence summary.
4. **Candidate Report** — click through from any ranked or Needs Review candidate for the full picture: exact score reconciliation, every piece of Evidence with its source location, and (for Ranked candidates) a **Generate Interview Questions** button — ten grounded questions, generated on demand, also polled live.
5. **Print** — from a Candidate Report, **Prepare to print** walks through a destination-confirmation step before producing a clean, print-ready record (candidate report only, no application chrome).

Notes on how the demo data model works:
- One Recruiter can only have **one active Analysis Session** at a time — finish or abandon the current one before starting another.
- A Failed candidate can be retried once, which starts a **new Analysis Revision** rather than mutating the original.
- Marking a candidate **Shortlisted** and marking an Evidence row **Disputed** are your own recorded decisions — the tool never infers or changes either automatically.

## Using a larger local model

The default model (`qwen2.5:0.5b-instruct`, ~400 MB) is deliberately small so it runs on any machine. If you have more RAM/GPU to spare, a larger model in the same family gives a real accuracy improvement — better instruction-following and citation precision — without needing any external account:

```sh
docker compose exec ollama ollama pull qwen2.5:14b-instruct   # or 7b / 32b depending on hardware, see below
```

Then set in `.env`:

```env
OLLAMA_MODEL=qwen2.5:14b-instruct
```

And rebuild the worker: `docker compose up -d --build worker`.

Sizing guide (no dedicated GPU):

| Available RAM | Model |
|---|---|
| 16 GB | `qwen2.5:7b-instruct` |
| 32 GB+ | `qwen2.5:14b-instruct` (recommended if available) |
| Strong GPU, 16 GB+ VRAM | `qwen2.5:32b-instruct` |

Stay within the `qwen2.5` family — the prompts this app sends were tuned against that family's specific behavior (e.g. its tendency to paraphrase instead of quoting verbatim), and a different model family may behave differently in ways that haven't been tuned for.

## Using Azure OpenAI instead of the local model

The app can use Azure OpenAI instead of the local model for meaningfully better accuracy (see "Known limitations" below) — recommended once you have real hiring decisions riding on the output. Set all four of these in `.env`:

```env
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your key>
AZURE_OPENAI_DEPLOYMENT=<your deployment name>
AZURE_OPENAI_API_VERSION=<e.g. 2024-10-21>
```

Then `docker compose up -d --build worker`. Presence of all four switches the worker over to Azure automatically; leaving any one blank keeps it on the local model. **As of this handover, this integration is not yet implemented** — the code path exists but doesn't call Azure yet, so setting these now will make analysis fail rather than switch providers. Treat this as documentation for a near-term follow-up, not a working option today.

## Known limitations (by design, for this local setup)

- **The local AI model is small and deliberately free of cost/API keys**, which trades off some accuracy and speed — occasional Needs Review outcomes on candidates that a stronger model would confidently rank, and tens-of-seconds-per-candidate processing time, are expected, not bugs. A larger local model or Azure OpenAI (above) are the upgrade paths.
- **No password-reset flow yet** — if you forget a password, register a new account and have it admitted again.
- **Single-host, private use only** — this isn't set up for a public server; keep it on a private machine/network.
- **Synthetic data first** — using real candidates' Resumes is a real privacy decision. Only load real Resume data once you've deliberately decided to, on a machine you control.

## Troubleshooting

- **`web` won't come up / build fails after an edit:** check `docker compose logs web` for the actual error, then `docker compose up -d --force-recreate --build web` to force a clean rebuild.
- **Stuck on "Analysis preparation is in progress" for a long time:** check the model actually finished pulling (`docker compose exec ollama ollama list` should list the model named in `OLLAMA_MODEL`, or `qwen2.5:0.5b-instruct` if unset), and check `docker compose logs worker` for errors.
- **Registered but can't sign in:** the account almost certainly hasn't been admitted yet — see step 5 above.
- **Forgot which email you admitted:** `docker compose exec postgres psql -U cvanalyzer -d cvanalyzer -c "SELECT email, admitted_at FROM users;"`.
