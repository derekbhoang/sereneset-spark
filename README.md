# SereneSet Spark (SSS)

An AI campaign asset workspace for marketing teams that turns campaign briefs and brand assets into brand-guided image and video campaign packs. Every generated asset is stored with version history, prompts, model metadata, review status, and export options, so teams can approve, refine, reuse, and audit their creative work over time.

## Problem

Marketing teams are under pressure to create more campaign assets for more channels, but the process is still fragmented. Briefs, brand guidelines, generated drafts, feedback, approvals, and final files often live across separate tools, making it hard to keep assets on-brand and understand how each version was created.

As teams adopt generative AI, this problem gets sharper: outputs can be fast, but they are often disconnected from brand context, review workflows, metadata, and long-term asset storage. Teams need a way to generate campaign materials while preserving version history, approval status, prompts, model details, and reusable campaign context.

## Solution

SereneSet Spark gives marketing teams a single workspace to turn campaign briefs and brand assets into structured, brand-guided campaign packs. Teams can generate images and videos from shared campaign context, then review, refine, approve, and export assets without losing the history behind each version.

Every generated asset is stored with useful metadata, including prompts, model details, campaign tags, brand inputs, review status, and version lineage. This makes the creative process easier to audit, reuse, and improve over time while helping teams move faster without losing control of brand consistency.

## How It Works

1. Teams create a dedicated campaign workspace for each launch, product, audience, or channel initiative. Each campaign keeps its own briefs, generated assets, feedback, approvals, metadata, and export history separated from other campaigns so the work stays easy to organize and review.

2. Teams add the campaign context, such as the brief, goals, audience, tone, channels, brand assets, and creative requirements. SereneSet Spark uses this context to guide image and video generation.

3. Teams review generated assets inside the campaign workspace. They can approve strong assets, request refinements, compare versions, and keep a clear record of how each asset changed over time.

4. Teams export approved campaign assets for publishing, handoff, or future reuse. Each exported asset remains connected to its campaign metadata, version history, prompts, model details, and review status.

5. Optionally, teams can create a shared library for reusable brand details, such as guidelines, product descriptions, tone of voice, audience profiles, disclaimers, logos, and reference assets. Each campaign can use this shared library to stay consistent without duplicating the same brand information every time.

## Key Features

- **Separated campaign workspaces:** Create and manage multiple campaigns independently, with each campaign keeping its own briefs, assets, approvals, metadata, and exports.
- **Shared brand library:** Store reusable brand guidelines, product details, tone of voice, audience profiles, and reference assets that every campaign can use.
- **Brand-guided media generation:** Generate images and videos using the campaign brief, uploaded references, previous versions, and attached brand assets.
- **Durable video jobs:** Submit long-running video generation to a PostgreSQL-backed worker and monitor progress without blocking the API.
- **Version history:** Keep track of every refinement, regenerated asset, and approved version so teams can see how creative work changed over time.
- **Review and approval workflow:** Mark assets as drafts, in review, approved, or rejected to support clearer collaboration between marketers, designers, and stakeholders.
- **Metadata-rich storage:** Save prompts, model details, campaign tags, brand inputs, review status, and asset lineage alongside each generated file.
- **Searchable asset organization:** Find assets by campaign, channel, status, format, audience, or tag for future review and reuse.
- **Export-ready campaign packs:** Package approved assets for publishing, stakeholder handoff, or reuse in future campaigns.

## Tech Stack

- **Frontend:** Vite React, TypeScript, and Tailwind CSS for a fast, responsive campaign workspace.
- **Backend:** FastAPI for the application API, campaign workflows, asset metadata, and generation requests.
- **Generative media orchestration:** Genblaze for connecting campaign context to AI media generation workflows.
- **Storage:** Backblaze B2 Cloud Storage for generated assets, uploaded brand files, campaign exports, and versioned media.
- **Metadata:** Structured campaign, asset, prompt, model, review, and version metadata stored alongside each asset.
- **AI provider:** GMI Cloud, orchestrated through Genblaze, for image and video generation.

## Implemented Workflow

The first version of SereneSet Spark focuses on a complete campaign asset workflow that can be demoed end to end:

1. Create and manage multiple separated campaign workspaces.
2. Add campaign details, including brief, audience, tone, channels, goals, and brand requirements.
3. Create an optional shared brand library with reusable guidelines, product details, audience profiles, and reference assets.
4. Generate images synchronously and submit video generation as durable background jobs through GMI Cloud and Genblaze.
5. Store generated assets and uploaded brand files in Backblaze B2 Cloud Storage.
6. Save useful metadata for each asset, including prompts, model details, campaign tags, version history, and review status.
7. Review generated assets and mark them as draft, in review, approved, or rejected.
8. Refine selected assets while preserving earlier versions.
9. Search and filter assets by campaign, channel, status, format, audience, or tag.
10. Export approved images, videos, brand assets, input snapshots, and provenance sidecars as a campaign pack.

Video generation runs asynchronously through a PostgreSQL-backed worker so long-running provider requests do not block the API process.

## Local Setup

### Prerequisites

- Python 3.12 or newer
- Node.js 22 or newer and npm
- Docker Desktop with Docker Compose
- A private Backblaze B2 bucket and an application key scoped to that bucket
- A GMI Cloud API key for live image and video generation

### 1. Start PostgreSQL

From the repository root:

```powershell
docker compose -f backend/docker-compose.yml up -d
```

This starts PostgreSQL 17 on `localhost:5432` with the development database and credentials already used by the default backend configuration.

### 2. Configure the backend

Create `backend/.env` and set the values for your B2 region and bucket. Do not commit this file.

```dotenv
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://sereneset:sereneset@localhost:5432/sereneset_spark

B2_ENDPOINT_URL=https://s3.us-east-005.backblazeb2.com
B2_REGION_NAME=us-east-005
B2_BUCKET_NAME=your-private-bucket
B2_APPLICATION_KEY_ID=your-key-id
B2_APPLICATION_KEY=your-application-key

GMI_API_KEY=your-gmi-api-key
GENBLAZE_IMAGE_MODEL=seedream-5.0-lite
GENBLAZE_VIDEO_MODEL=veo-3.1-fast-generate-001
GENBLAZE_VIDEO_EDIT_MODEL=wan2.7-videoedit
GENBLAZE_VIDEO_TO_VIDEO_ENABLED=false
GENBLAZE_TIMEOUT_SECONDS=600
GENBLAZE_VIDEO_TIMEOUT_SECONDS=900
GENBLAZE_STORAGE_PREFIX=sereneset-spark/genblaze
MAX_VIDEO_SOURCE_IMAGE_SIZE_BYTES=26214400
MAX_VIDEO_SOURCE_VIDEO_SIZE_BYTES=104857600

CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
```

The B2 endpoint and region must match the bucket. The application key needs read/write access to media objects; readiness checks also require the `listAllBucketNames` capability.

Before enabling a new video input mode, inspect the live GMI model contract. This is a read-only check and does not submit a paid generation request:

```powershell
Set-Location backend
python -m scripts.check_gmi_video_model
python -m scripts.check_gmi_video_model --model veo-3.1-fast-generate-001
```

The configured edit model, `wan2.7-videoedit`, reports an active video model with a required `video` input parameter and video output. The ordinary `veo-3.1-fast-generate-001` model supports text-to-video and image-to-video, but not video-to-video. Upstream model contracts can change, so run the check again before deployment.

The backend has an exact capability entry for `wan2.7-videoedit`. When video-to-video is enabled, the worker revalidates the queued input mode and immutable source metadata, signs the stored B2 MP4 only for the active attempt, then validates the signed input before calling Genblaze. The model spec maps that URL to GMICloud's required `video` payload parameter. The provider payload is restricted to the verified `prompt` and `video` fields; signed URL query strings are not copied into provenance or worker failure metadata.

`GENBLAZE_VIDEO_TO_VIDEO_ENABLED` remains an operational opt-in. Set it to `true` for both the API and video worker only after rerunning the model-contract check, then redeploy both processes. An unregistered model, a mismatched `GENBLAZE_VIDEO_EDIT_MODEL`, or a disabled flag still returns `422` before a paid job is queued. The API never silently converts a video to one frame or submits a job that would ignore the video.

### 3. Install and migrate the backend

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
alembic upgrade head
```

### 4. Run the API and video worker

Open two PowerShell terminals in `backend`, activate `.venv` in each, and run:

```powershell
# Terminal 1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2
python -m app.workers.video_generation
```

The worker is required for video jobs and for `/api/v1/health/ready` to report ready.

### 5. Run the frontend

In a third terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The development frontend uses `http://127.0.0.1:8000/api/v1` by default. API documentation is available at `http://127.0.0.1:8000/docs`.

### 6. Verify the services

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

Readiness should report `ok` for PostgreSQL, B2, and the video worker. You can now create a campaign, attach brand assets, generate or refine an image, submit a video job, inspect provenance, approve versions, and export the campaign pack.

## Demo Showcase Seed

Run the migrations, configure PostgreSQL and B2 in `backend/.env`, then seed the polished showcase campaign:

```powershell
Set-Location backend
alembic upgrade head
python scripts/seed.py --showcase-only
```

The backend production image includes the seed script and fixtures. Against a running production Compose stack, the equivalent command is:

```powershell
docker compose --env-file .env.production -f compose.production.yml run --rm api python scripts/seed.py --showcase-only
```

The showcase includes three attached brand assets, two approved image versions, a playable approved video, immutable input snapshots, a completed video job, SHA-256 provenance, B2 manifests and sidecars, and artifacts that can be previewed and exported from the UI. The bundled media is explicitly identified in provenance as deterministic demo fixtures, not a live provider run.

The seed uses stable UUIDs and deterministic B2 keys. Running the same command again updates the same rows and overwrites the same objects without creating duplicates. To generate and verify a campaign pack at seed time, add an output path:

```powershell
python scripts/seed.py --showcase-only --export-pack ..\sereneset-showcase.zip
```

Running without `--showcase-only` also seeds the original lightweight example campaigns. `--metadata-only` skips B2 writes for database-only development, but its showcase media cannot be previewed or exported until the command is rerun with B2 enabled.

### Different Device Test

For a final same-network rehearsal, deploy the production API, worker, migration, and frontend images with an isolated PostgreSQL container:

```powershell
.\scripts\deploy-device-test.ps1
```

The launcher reads B2 and provider credentials directly from `backend/.env`, waits for `/api/v1/health/ready`, seeds the showcase, and prints the local and private-LAN URLs. It does not create another secrets file. Connect a phone, tablet, or second computer to the same network and open the printed LAN URL. Windows may ask once for permission to allow Docker Desktop on private networks.

Run the deployed workflow smoke test through the LAN address before using the second device:

```powershell
python scripts\test-deployed-workflow.py --base-url http://192.168.68.104:8080
```

The smoke test verifies readiness, campaign CRUD, the public `status` filter, brand files, image and video artifacts, stored sidecars, provenance hashes, the completed worker job, and every file referenced by the exported campaign pack. It uses the deterministic showcase and does not submit paid generation requests.

This overlay is for device testing only. It sets `ENVIRONMENT=development` so the containers may use its local PostgreSQL service; the production Compose file continues to require managed PostgreSQL with TLS. Stop the rehearsal without deleting its database volume:

```powershell
$env:ENV_FILE = (Resolve-Path backend\.env).Path
docker compose --env-file backend\.env -f compose.production.yml -f compose.device-test.yml down
```

## Development Checks

Install backend development dependencies and run the backend suite:

```powershell
Set-Location backend
python -m pip install -r requirements-dev.txt
python -m pytest
```

Run the frontend checks from `frontend`:

```powershell
npm ci
npm run typecheck
npm run lint
npm run build
```

The GitHub Actions workflow runs these checks in parallel on every push and pull request.

## Render Deployment

[`render.yaml`](render.yaml) defines the budget-conscious judge-facing deployment in Render's Virginia region:

- `sereneset-spark`: free static frontend served from Render's CDN over managed HTTPS.
- `sereneset-spark-api`: paid Starter Docker web service that migrates the database and idempotently refreshes the showcase before each release.
- `sereneset-spark-video-worker`: private background worker that waits for API liveness before claiming PostgreSQL jobs.
- `sereneset-spark-postgres`: free private managed PostgreSQL 17 database with no external IP allowlist.

Every API deployment runs migrations and then the idempotent showcase seed. This keeps a failed one-time setup hook from leaving the judge-facing database empty. B2 remains the durable store for all uploaded and generated media, sidecars, provenance, and export inputs.

After pushing `render.yaml` to GitHub, open the [Render Blueprint launcher](https://render.com/deploy?repo=https://github.com/derekbhoang/sereneset-spark), connect the repository, and confirm the paid resources. During initial creation, Render prompts for these values once on the API service:

- `B2_BUCKET_NAME`
- `B2_APPLICATION_KEY_ID`
- `B2_APPLICATION_KEY`
- `GMI_API_KEY`

The worker references those secrets without duplicating them. Render injects the generated API and frontend URLs into the Vite build and FastAPI CORS allowlist, so suffixed `onrender.com` hostnames work without manual configuration. Keep those generated hostnames for the initial submission; Render provisions and renews their TLS certificates automatically.

Wait for the API pre-deploy migration and seed command to finish, then open the frontend URL and verify:

```text
https://sereneset-spark-api.onrender.com/api/v1/health/ready
```

If Render adds a suffix to the service name, use the exact URL shown in its dashboard. Readiness should report `ok` for PostgreSQL, B2, and the video worker before sharing the app. The Blueprint costs approximately $14 for 30 days: $7 each for the API and worker, with a free static frontend and free PostgreSQL. The database is limited to 1 GB, has no backups, and expires after 30 days; upgrade it before expiry if the deployment must remain available longer.

Run the complete read/download/export/CRUD acceptance check against the deployed origins without invoking paid generation:

```powershell
python scripts\test-deployed-workflow.py `
  --base-url https://sereneset-spark.onrender.com `
  --api-url https://sereneset-spark-api.onrender.com `
  --timeout-seconds 120
```

## Production Containers

The production Compose stack runs three long-lived services and one release process:

- `migrate`: runs `alembic upgrade head` once and must complete successfully before the API or worker starts.
- `api`: FastAPI served by Uvicorn on the private Compose network.
- `worker`: the PostgreSQL-backed video generation worker, built from the same backend image as the API.
- `frontend`: a Vite production build served by Nginx on port `8080`. Nginx forwards same-origin `/api` requests to the API service.

### 1. Provision external services

Before deploying containers:

1. Create a managed PostgreSQL database, allow connections from the deployment environment, and obtain a TLS-enabled connection URL.
2. Create a private B2 bucket and a bucket-scoped application key with read/write access plus `listAllBucketNames` for readiness probes.
3. Obtain a GMI Cloud API key with access to the configured image and video models.
4. Choose the public application hostname and configure TLS at the platform load balancer or reverse proxy.

Production media must remain in B2. PostgreSQL stores relational records, jobs, object keys, and provenance indexes, but not generated media blobs.

### 2. Configure deployment secrets

Create the production environment file and replace every placeholder value:

```powershell
Copy-Item .env.production.example .env.production
```

Keep `.env.production` out of source control. Set `APP_DOMAIN` and `CORS_ORIGINS` to the public HTTPS hostname, use a managed `DATABASE_URL` with `sslmode=require` or stronger verification, and use the B2 endpoint and region belonging to the configured bucket.

### 3. Build, migrate, and start

From the repository root:

```powershell
docker compose --env-file .env.production -f compose.production.yml up -d --build
```

Compose runs the `migrate` release process automatically. The API and worker remain stopped if Alembic exits with an error, preventing application code from starting against an outdated schema. On deployment platforms with a dedicated release phase, use the same backend image with `alembic upgrade head` as its release command.

### 4. Verify the deployment

Inspect process state and logs:

```powershell
docker compose --env-file .env.production -f compose.production.yml ps --all
docker compose --env-file .env.production -f compose.production.yml logs -f migrate api worker frontend
```

The `migrate` container should exit with code `0`; `api` and `frontend` should become healthy; and `worker` should remain running. Verify readiness through the public origin:

```powershell
Invoke-RestMethod https://spark.example.com/api/v1/health/ready
```

Then test the complete workflow in a private or signed-out browser session: create a campaign, upload and attach a brand asset, generate an image, refine it, submit a video, wait for the job to succeed, play both artifacts, inspect stored provenance, approve the versions, and download the export pack. Confirm that generated files and sidecars exist in B2 and that no browser request uses a localhost API URL.

The frontend container listens on host port `APP_PORT` (default `8080`). Do not expose the API container separately; Nginx proxies same-origin `/api` requests to it. Terminate TLS at the deployment platform or an external reverse proxy in front of the frontend port.

### 5. Deploy updates

Build the new image version and rerun Compose with the updated `APP_VERSION`. The one-shot migration process runs before the updated API and worker start:

```powershell
$env:APP_VERSION = "2026.07.16"
docker compose --env-file .env.production -f compose.production.yml up -d --build
```

Review migration and application logs after every deployment. Database migrations must remain compatible with the currently running application during rolling deployments.

Stop the application containers without deleting managed data or B2 objects:

```powershell
docker compose --env-file .env.production -f compose.production.yml down
```

### Production Data Services

Production intentionally does not run PostgreSQL or durable object storage inside the Compose stack. Configure these external services in `.env.production`:

- `DATABASE_URL` must point to managed PostgreSQL. Public database connections use `DATABASE_CONNECTION_MODE=tls` and must include `sslmode=require`, `sslmode=verify-ca`, or `sslmode=verify-full`. Render uses `DATABASE_CONNECTION_MODE=private` with its dotless internal DNS hostname and blocks external database access. Provider URLs beginning with `postgres://` or `postgresql://` are normalized to the installed `psycopg` driver.
- PostgreSQL stores campaigns, asset/version records, review state, generation jobs, provenance indexes, and B2 object keys. It does not store generated media blobs.
- Backblaze B2 stores uploaded inputs, brand files, generated image and video artifacts, metadata sidecars, and export inputs. Use a private bucket and bucket-scoped application credentials.
- Production startup fails when PostgreSQL points to a local host, a public connection does not require TLS, a private connection uses a public hostname, or any required B2 setting is empty or still contains an example placeholder.

Database pools are bounded per process. With the example values, two API workers and one generation worker can open at most 15 PostgreSQL connections: `(2 + 1) * (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)`. Keep this total below the managed database connection limit when changing worker counts or `WEB_CONCURRENCY`.

### Health Checks

- `GET /api/v1/health` is a process liveness check. It does not contact PostgreSQL, B2, or the worker and is used for internal Compose startup ordering.
- `GET /api/v1/health/ready` is the external readiness check. It returns `200` only when PostgreSQL answers a query, the configured B2 bucket accepts a read-only `Head Bucket` request, and the video worker heartbeat is fresh. Otherwise it returns `503` with a status for each component.
- The worker publishes its heartbeat from a background thread, so readiness remains healthy while video generation is blocked on a long provider request.

Use `WORKER_HEARTBEAT_INTERVAL_SECONDS` to control publication frequency and `WORKER_HEARTBEAT_STALE_AFTER_SECONDS` to control failure detection. The stale window must be greater than the publication interval. Use the readiness route for deployment traffic checks, but keep worker startup tied to the liveness route to avoid a circular dependency.

`B2_READINESS_TIMEOUT_SECONDS` bounds the read-only B2 probe and disables retries for that probe only. Normal media operations continue to use the standard storage client configuration.

For a bucket-restricted B2 application key, enable the `listAllBucketNames` capability required by S3-compatible `Head Bucket` requests. See the [Backblaze app key capability reference](https://www.backblaze.com/docs/cloud-storage-s3-compatible-app-keys).
