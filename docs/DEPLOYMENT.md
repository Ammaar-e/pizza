# Deployment on AWS EC2 (Ubuntu)

PostgreSQL runs **directly on EC2** in both options. No RDS, Aurora or other managed SQL service is
used, and there is no per-restaurant instance: all franchisees and stores share one deployment.

## Option A — single instance

Recommended for demos and a handful of stores. Use m7i.large (2 vCPU / 8 GB) or larger.

1. Launch Ubuntu 24.04 LTS. Attach a separate **gp3 EBS volume** (e.g. 100 GB), format it and mount it at
   `/srv/pizza`. Enable EBS snapshots (Data Lifecycle Manager) as a second layer of backup.
2. Security group: inbound 443 and 80 from the internet, 22 from your admin IP only.
3. Install Docker Engine + Compose plugin (`docs.docker.com/engine/install/ubuntu`).
4. Clone the repository, then:
   ```bash
   cd deploy && cp .env.example .env && chmod 600 .env   # fill in every value
   sudo mkdir -p /srv/pizza/{pgdata,backups,certs,acme}
   ```
5. TLS: put `fullchain.pem` / `privkey.pem` in `/srv/pizza/certs`. For Let's Encrypt, use
   certbot's webroot mode with `/srv/pizza/acme` and a renewal cron that reloads nginx.
6. `docker compose up -d --build`. The `migrate` service runs migrations, which also seed roles,
   permissions and providers.
7. Create the first HQ administrator:
   `docker compose exec backend python manage.py createsuperuser`, then assign the `hq_admin` role
   in `/admin/`. Alternatively load demo data with
   `docker compose exec backend python manage.py seed_demo --password '<strong password>'`
   (demonstrations only).

## Option B — two instances

| EC2 #1 (application) | EC2 #2 (database) |
|---|---|
| nginx, Next.js, Daphne (API + WebSockets), Celery worker + beat | PostgreSQL 16, Redis, backup job |
| `docker compose -f docker-compose.app.yml up -d --build` | `PRIVATE_IP=<private ip> docker compose -f docker-compose.db.yml up -d` |

* Put both in the same VPC. Place EC2 #2 in a **private subnet** without a public IP.
* EC2 #2 security group: allow 5432 and 6379 **only from EC2 #1's security group**, plus 22 from a
  bastion. Ports are bound to the private IP (`PRIVATE_IP`), and `pg_hba.conf` limits sources to the
  VPC range (narrow it to your subnet).
* On EC2 #1, `.env` sets `POSTGRES_HOST=<db private ip>` and
  `REDIS_URL=redis://:<REDIS_PASSWORD>@<db private ip>:6379/0`. Use `POSTGRES_SSLMODE=require` after
  enabling SSL in PostgreSQL if your policy requires encryption inside the VPC.
* Data lives on a dedicated EBS volume mounted at `/srv/pizza` on EC2 #2.

## Backups and restoration

* The `backup` service runs `deploy/scripts/backup.sh` every `BACKUP_INTERVAL_SECONDS` (default 6 h).
  It makes a `pg_dump --format=custom` dump, verifies it with `pg_restore --list`, writes a SHA-256
  checksum and applies retention (`BACKUP_RETENTION_DAYS`).
* **Restoration is tested automatically**: once a week `restore-test.sh` restores the newest dump into a
  throwaway database, runs sanity queries and drops it. Run it on demand with
  `docker compose exec backup sh /scripts/restore-test.sh`.
* Off-instance copies: sync `/srv/pizza/backups` to an S3 bucket from the host
  (`aws s3 sync /srv/pizza/backups s3://<bucket>/pizza/` in cron, using the instance role). Also use EBS snapshots.
* Disaster recovery:
  ```bash
  docker compose stop backend worker beat
  docker compose run --rm -e CONFIRM=yes backup sh /scripts/restore.sh /backups/<file>.dump
  docker compose up -d
  ```

## Secrets

* Keep `.env` at mode 600, owned by root. For stronger management, render it at boot from AWS
  Systems Manager Parameter Store / Secrets Manager (these store secrets only; the database stays on EC2).
* `FIELD_ENCRYPTION_KEYS`: to rotate, prepend a new key, restart, then re-save credentials.
  `apps.core.encryption.rotate_token` re-encrypts values with the new key.
* Never reuse the demo passwords from `seed_demo` in production.

## Provider webhook endpoints

After deploying, open the franchise portal → **Delivery integrations** → copy each account's webhook
URL into the provider's developer portal (Uber primary webhook URL; DoorDash webhook subscriptions
for Orders, Menu Status, Menu Request and Order Canceled, with the auth token).

## Upgrades

```bash
git pull && cd deploy && docker compose up -d --build   # migrate runs before backend/worker start
```

## Operations

* Health: `GET /healthz/` (checks the database).
* Logs: `docker compose logs -f backend worker`. Production logs are JSON with secrets redacted.
  Integration errors are also stored in the database (portal → integrations → error history) and in
  the HQ **Delivery providers** monitor (with retry).
* Celery beat runs the provider-deadline sweeper every 60 s, failed-webhook retries every 2 min and
  outbound API-client webhook retries every 60 s.
