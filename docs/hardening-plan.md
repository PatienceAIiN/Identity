# Production hardening — what's outstanding, and what each item costs

Written after the first real-camera confirmation (Google Lens, 2026-08-11). The
core claim is now validated, which changes the priority order: the product is
worth hardening.

Each item states the actual work, not a wish. Nothing here is done unless marked.

## 1. Photos out of Postgres, into R2 — **do this first**

**Now:** `photos.image_png` is a `LargeBinary` column. Every fused PNG (~1–2 MB)
lives in the database on a free-tier VM.

**Why it matters more than it looks:** it inflates every backup, makes restores
slow, and puts the largest objects on the machine with the least headroom. The
spec (§8.7) asks for object storage with server-side encryption and short-lived
URLs; we already have the R2 bucket and credentials in use for APKs.

**The work:**
- Add `photos/` as a prefix in the existing `identity` bucket (the APK code in
  `apps/api/releases.py` already has a working `Storage` class — reuse it rather
  than writing a second S3 client).
- Replace `image_png` with `object_key`; serve `/v1/photos/{id}.png` as a 302 to
  a presigned URL with a short TTL rather than streaming bytes.
- Migration: walk existing rows, upload each blob, set `object_key`, then drop
  the column in a second deploy. Two deploys, because dropping a column in the
  same release as the code that stops reading it leaves no rollback.
- Deletion paths must delete the object too: `delete_code`, `delete_me`, and the
  admin cascade in `platformapi.delete_user_cascade`. A test should assert the
  object is gone, or this leaks storage on every delete.

**Effort:** half a day, most of it in the migration and the delete paths.

## 2. Encoding off the request path

**Now:** `encode_photo` runs inline. Fusion plus five decode validations takes
seconds, holding a Cloud Run request open the whole time.

**The work:** a job table (`encode_jobs`) plus a poller, not a broker — there is
no Redis here and adding one to a free-tier VM is worse than the problem. `POST
/v1/codes` inserts a job and returns `202` with a job id; the client polls
`/v1/codes/jobs/{id}`. Both clients need a waiting state, which the app already
has for other flows.

**Effort:** a day, and it changes the client contract — so it wants doing before
there are integrators depending on the synchronous `201`.

## 3. Shared state for the limiter, nonces and IP salt

**Now:** all three are in-process dicts in `main.py` and `platformapi.py`. Correct
on one instance; on two, the rate limit doubles, a replayed nonce can land on the
other instance, and IP buckets stop agreeing.

**The work:** move to Postgres tables with a small TTL sweep. `seen_nonces`
becomes a table with a unique constraint — an insert that violates it *is* the
replay check, which is simpler and stricter than a lookup. The IP salt moves to
the deployment secret so every instance hashes identically.

**Effort:** half a day. Until then, Cloud Run must stay capped at one instance —
that is a live constraint, not a theoretical one.

## 4. Error tracking and alerting

**Now:** nothing. Failures are visible only by reading Cloud Run logs by hand,
which is how the foreign-key bug survived three days.

**The work:** a Sentry (or GCP Error Reporting) hook on the exception handler,
scrubbing URL fragments before send — a fragment in an error report is a leaked
key, and `scrub_fragment` already exists for exactly this. Then two alerts worth
waking up for: 5xx rate over baseline, and `/v1/health` failing.

**Effort:** two hours. Highest value per hour of anything on this list.

## 5. Backups, verified

**Now:** Postgres on the VM with no verified restore. An unverified backup is a
belief, not a backup.

**The work:** nightly `pg_dump` to R2 with a retention window, plus a documented
restore drill run once end-to-end into a scratch database, with the result and
the time-to-restore written down.

**Effort:** half a day including the drill. Do the drill; a backup nobody has
restored has a way of not being one.

## 6. Load test

**Now:** never load tested. The concurrency ceiling is unknown, and fusion is
CPU-bound on one small instance.

**The work:** k6 or Locust against a staging deploy: `/r/{id}` at 50 rps (the
public path, and the one an enumeration attempt would hit), plus a handful of
concurrent creates to find where encoding queues. Record the numbers so the
scan-cap and rate-limit values are chosen from evidence rather than taste.

**Effort:** half a day.

## 7. Rollback drill

**Now:** revisions exist and traffic can be shifted, but nobody has done it under
pressure. The APK side keeps one prior build for exactly this reason.

**The work:** deploy a deliberately broken revision to staging, roll back with
`gcloud run services update-traffic`, time it, write the command down where
someone panicking can find it.

**Effort:** an hour.

---

## Order

1. Error tracking (2h, highest value per hour)
2. Photos to R2 (half a day, largest structural debt)
3. Shared state (half a day, unblocks more than one instance)
4. Backups + restore drill (half a day)
5. Encoding off the request path (a day, before external integrators)
6. Load test, rollback drill (a day together)

Roughly four focused days. Until 3 is done, **keep max instances at 1**.
