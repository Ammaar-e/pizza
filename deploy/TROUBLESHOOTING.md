# Troubleshooting: API down / "502 Bad Gateway"

## What the symptom means

At `https://pos.amrt.ca`, the website pages load, but every API call returns **502**: `/api/v1/docs/`,
creating franchisees, sign-in, and so on. The request path is:

```
browser / tablet ──▶ Cloudflare ──▶ nginx (container "nginx") ──┬─▶ frontend:3000  (website)      ✔ works
                                                               └─▶ backend:8000   (Django API)   ✘ 502
```

A 502 on `/api/...` while `/` still works means **nginx is running but cannot get an answer from the
`backend` container**. The application code itself starts correctly in production mode: migrations,
`manage.py check --deploy` and Daphne serving `/healthz/` and `/api/v1/docs/` were all verified. The cause is
on the server. It is one of the items below, and the diagnosis script finds which one.

After this update, nginx answers API outages with JSON
`{"error":{"code":"backend_unavailable",…}}` instead of an HTML page, so the portal and the POS apps show a
clear message.

## 1. Run the diagnosis (on the EC2 server)

```bash
cd /path/to/pizza/deploy
```

```bash
sh scripts/diagnose.sh
```

On a two-instance deployment (Option B), on the application host:

```bash
COMPOSE_FILE=docker-compose.app.yml sh scripts/diagnose.sh
```

The script is read-only. It shows container states, the `migrate` and `backend` logs, which required `.env`
settings are missing (names only, never values), whether nginx can reach the backend, the database and
migrations, disk and memory.

## 2. Fix by cause

### A. `migrate` failed, so the backend never started (most common after a deploy)

`backend`, `worker` and `beat` wait for `migrate` to **complete successfully**. If it failed, they are
never started, and nginx returns 502 for every API call.

```bash
docker compose logs --tail 100 migrate
```

Fix the error shown (usually database connection or credentials; see C), then:

```bash
docker compose up -d
```

### B. The backend starts and then exits (crash loop)

```bash
docker compose logs --tail 100 backend
```

In production, Django **refuses to start** without `DJANGO_SECRET_KEY` and `FIELD_ENCRYPTION_KEYS`. Fill
`deploy/.env` from `.env.example`. Generate values with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then:

```bash
docker compose up -d --force-recreate backend worker beat
```

> If `FIELD_ENCRYPTION_KEYS` already had a value, **do not replace it**. Stored provider and payment
> credentials can only be decrypted with the existing key. To rotate, *prepend* a new key.

### C. Database unreachable or wrong password

The `migrate` or `backend` logs show `could not connect to server` or `password authentication failed`.

- **Option A:**
  ```bash
  docker compose ps postgres
  ```
  ```bash
  docker compose logs --tail 50 postgres
  ```
  The data directory (`PG_DATA_DIR`, default `/srv/pizza/pgdata`) must be on the mounted EBS volume.
  `POSTGRES_PASSWORD` in `.env` must match the password the cluster was **first initialised with**.
  Changing it in `.env` afterwards does not change it inside PostgreSQL.
- **Option B:** `POSTGRES_HOST` must be the database instance's **private IP**. Its security group must
  allow 5432 from the app instance's security group, and `pg_hba.conf` must allow the VPC range.

### D. nginx still points at an old backend address

After `docker compose up -d --build`, the backend container gets a new IP. The previous nginx config
resolved `backend` only once, at start-up, and kept sending traffic to the old address.

The new `nginx/nginx.conf` re-resolves through Docker's DNS every 10 s, which prevents this. Apply it with:

```bash
docker compose up -d --force-recreate nginx
```

### E. Out of memory or disk

```bash
free -m
```

```bash
df -h
```

```bash
dmesg | grep -i oom
```

If the kernel killed the backend (out of memory), use at least 4 GB RAM (m7i.large recommended) or add swap.
If the disk is full, remove old images:

```bash
docker image prune -f
```

```bash
docker builder prune -f
```

Make sure `/srv/pizza` is on the data volume.

### F. Not a 502, but a 400 or 403

- **400 Bad Request:** the host name is missing from `DJANGO_ALLOWED_HOSTS`. Use, for example,
  `pos.amrt.ca,backend,localhost`.
- **403 CSRF** on `/admin/`: add `https://pos.amrt.ca` to `DJANGO_CSRF_TRUSTED_ORIGINS`.
- **CORS errors** in the browser from another origin: add that origin to `CORS_ALLOWED_ORIGINS`.

## 3. Deploy this update

```bash
git pull
```

```bash
cd deploy && docker compose up -d --build
```

```bash
docker compose up -d --force-recreate nginx
```

```bash
docker compose ps
```

Check the API:

```bash
curl -fsS https://pos.amrt.ca/healthz/
```

This should print `{"status": "ok"}`. Then open `https://pos.amrt.ca/api/v1/docs/`.

## 4. Cloudflare notes

- A **Cloudflare-branded** "502 Bad gateway / Host error" page means Cloudflare could not reach nginx at
  all: the instance is down, port 443 is closed in the security group, or there is a TLS problem.
  An **nginx** 502 (or the JSON `backend_unavailable` error) means nginx is fine and the backend is the
  problem (section 2).
- With Cloudflare SSL mode **Full (strict)**, the origin certificate in `/srv/pizza/certs` must be valid
  for `pos.amrt.ca`. A Cloudflare Origin Certificate works.
- Cloudflare times out after 100 s. API requests are far shorter, so a timeout points to a hung backend
  (see section 2B).
