# Deploying Identity

Pushes to `main` run the test suite and then deploy to Cloud Run
(`.github/workflows/deploy.yml`). Until the three secrets below exist, a push is
tested but not deployed — the workflow says so in its output rather than failing.

## One-time GCP setup

Run these once, as a project owner. They create a deploy identity that GitHub can
borrow for the length of one job, with no key file to leak.

```bash
PROJECT=gen-lang-client-0839484503
REPO=PatienceAIiN/Identity
NUM=$(gcloud projects describe $PROJECT --format='value(projectNumber)')

gcloud iam service-accounts create deployer --project $PROJECT \
  --display-name "GitHub Actions deployer"

for role in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer roles/iam.serviceAccountUser \
            roles/storage.admin; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member "serviceAccount:deployer@$PROJECT.iam.gserviceaccount.com" \
    --role $role
done

gcloud iam workload-identity-pools create github --project $PROJECT \
  --location global --display-name "GitHub"

gcloud iam workload-identity-pools providers create-oidc github \
  --project $PROJECT --location global --workload-identity-pool github \
  --display-name "GitHub OIDC" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='$REPO'" \
  --issuer-uri "https://token.actions.githubusercontent.com"

gcloud iam service-accounts add-iam-policy-binding \
  deployer@$PROJECT.iam.gserviceaccount.com --project $PROJECT \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github/attributes/repository/$REPO"

echo "GCP_WIF_PROVIDER = projects/$NUM/locations/global/workloadIdentityPools/github/providers/github"
echo "GCP_DEPLOY_SA    = deployer@$PROJECT.iam.gserviceaccount.com"
echo "GCP_PROJECT_ID   = $PROJECT"
```

The `attribute-condition` matters: without it, any repository could borrow this
identity.

Add the three printed values as repository secrets under
**Settings → Secrets and variables → Actions**.

## What the workflow will not do

It never passes `--set-env-vars`. That flag replaces the whole environment rather
than adding to it, and would silently drop the database password, the admin
password hash and the API signing key from the running service. Environment
changes stay a deliberate manual step.

## Secrets that live only on the service

Set once with `gcloud run services update identity --update-env-vars`:

| Variable | Purpose |
|---|---|
| `PHOTOBIND_ADMIN_PASSWORD_HASH` | argon2id hash of the operator password |
| `PHOTOBIND_API_SIGNING_KEY` | 32-byte base64url key that encrypts API key secrets |
| `PHOTOBIND_GOOGLE_CLIENT_ID` | Google OAuth web client id |
| `PHOTOBIND_BREVO_API_KEY` | transactional email |
| `PHOTOBIND_DB_URL` | Postgres on the VM over Direct VPC egress |
| `PHOTOBIND_R2_*` | Cloudflare R2 credentials for APK releases |

Rotating `PHOTOBIND_API_SIGNING_KEY` invalidates every existing API key secret,
because those secrets are encrypted with it and cannot be recovered. Revoke and
reissue keys if you rotate it.
