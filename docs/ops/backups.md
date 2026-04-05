# Backup & Restore — SB‑Optima

## Overview

SB‑Optima stores all persistent state in a **PostgreSQL** database.  This
document covers backup procedures, restore steps, and verification.

> **Important:** The database contains encrypted credentials
> (`hostedshop_credentials` table) and billing metadata.  Treat all backups
> as **sensitive** and store them in an encrypted, access-controlled location.

---

## 1. Recommended cadence

| Environment | Frequency         | Retention |
|-------------|-------------------|-----------|
| Production  | Daily + before deploy | 30 days   |
| Staging     | Weekly            | 14 days   |

Automate with cron, Kubernetes CronJobs, or your cloud provider's managed
backup feature (e.g., AWS RDS automated snapshots).

---

## 2. Backup with `pg_dump`

### Full database (custom format — recommended)

```bash
pg_dump \
  --host=DB_HOST \
  --port=5432 \
  --username=DB_USER \
  --dbname=sboptima \
  --format=custom \
  --file=sboptima_$(date +%Y%m%d_%H%M%S).dump
```

### Plain SQL (for inspection / small databases)

```bash
pg_dump \
  --host=DB_HOST \
  --port=5432 \
  --username=DB_USER \
  --dbname=sboptima \
  --format=plain \
  --file=sboptima_$(date +%Y%m%d_%H%M%S).sql
```

### Schema-only (useful for audits)

```bash
pg_dump --schema-only \
  --host=DB_HOST \
  --username=DB_USER \
  --dbname=sboptima \
  --file=sboptima_schema.sql
```

### Environment variables

Set `PGPASSWORD` or use a `.pgpass` file to avoid interactive password
prompts in automated scripts:

```bash
export PGPASSWORD="$POSTGRES_PASSWORD"
```

---

## 3. Restore with `pg_restore`

### Into a fresh database

```bash
# 1. Create a new empty database
createdb --host=DB_HOST --username=DB_USER sboptima_restored

# 2. Restore from the custom-format dump
pg_restore \
  --host=DB_HOST \
  --username=DB_USER \
  --dbname=sboptima_restored \
  --no-owner \
  --no-privileges \
  sboptima_20250401_030000.dump
```

### Into the existing database (overwrite)

> ⚠️ **Destructive** — this drops and recreates objects.

```bash
pg_restore \
  --host=DB_HOST \
  --username=DB_USER \
  --dbname=sboptima \
  --clean \
  --no-owner \
  --no-privileges \
  sboptima_20250401_030000.dump
```

### From plain SQL

```bash
psql \
  --host=DB_HOST \
  --username=DB_USER \
  --dbname=sboptima_restored \
  --file=sboptima_20250401_030000.sql
```

---

## 4. Post-restore verification

After restoring, run these checks:

### A. Apply pending migrations

```bash
export DATABASE_URL="postgresql://DB_USER:DB_PASS@DB_HOST:5432/sboptima_restored"
alembic upgrade head
```

If the backup is from an older schema version, Alembic will apply any
migrations created after the backup was taken.

### B. Smoke checks

```bash
# Start the backend
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Health check
curl http://localhost:8000/health
# Expected: {"status":"ok","db":"ok"}

# Tenant count (requires auth)
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8000/admin/diagnostics
```

### C. Row counts

Connect to the restored DB and verify table row counts roughly match
expectations:

```sql
SELECT 'tenants' AS tbl, COUNT(*) FROM tenants
UNION ALL SELECT 'users', COUNT(*) FROM users
UNION ALL SELECT 'hostedshop_credentials', COUNT(*) FROM hostedshop_credentials
UNION ALL SELECT 'optimization_jobs', COUNT(*) FROM optimization_jobs
UNION ALL SELECT 'apply_batches', COUNT(*) FROM apply_batches
UNION ALL SELECT 'audit_events', COUNT(*) FROM audit_events;
```

---

## 5. Sensitive data considerations

| Table                    | Sensitive columns                          |
|--------------------------|--------------------------------------------|
| `users`                  | `email`, `password_hash`                   |
| `hostedshop_credentials` | `api_username_enc`, `api_password_enc`     |
| `tenants`                | `stripe_customer_id`, `stripe_subscription_id` |

- Credentials are encrypted at rest (Fernet), but the encryption key is
  stored separately (`ENCRYPTION_KEY` env var).  The backup alone cannot
  decrypt them.
- **Never** store backups in unencrypted S3 buckets or public locations.
- Consider encrypting dump files with `gpg` or using an encrypted storage
  backend.

---

## 6. Disaster recovery checklist

1. ✅ Restore the latest backup to a fresh database.
2. ✅ Run `alembic upgrade head` to apply any pending migrations.
3. ✅ Set the required environment variables (`DATABASE_URL`,
   `ENCRYPTION_KEY`, `JWT_SECRET`, etc.).
4. ✅ Start the backend and verify `/health` returns `{"db": "ok"}`.
5. ✅ Verify tenant and user counts match expectations.
6. ✅ Test a login flow to confirm JWT + password hashes work.
7. ✅ Test credential decryption by listing credentials for a known tenant.
