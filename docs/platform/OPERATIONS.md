# Hidden Tower Defence Platform Operations

## Production entrypoints

- Cloud Run URL:
  `https://hiddentowerdefence-588376054847.us-central1.run.app`
- Liveness: `/health`
- Readiness: `/readyz`
- Terraform backend:
  `gs://smp-substrate-tfstate-prod/products/hiddentowerdefence/prod`

Do not use `/healthz` for external monitoring: Google's edge reserves that path
and returns a non-application 404. The application preserves `/healthz` only
for local compatibility.

## Standard deployment

The `Deploy production` GitHub workflow is the normal path after a change
reaches `main`:

1. OIDC authenticates to the dedicated deployer identity.
2. The image is built once and pushed.
3. The captured digest is passed to Terraform.
4. Terraform applies the service revision.
5. `/health` and `/readyz` are checked.
6. The digest and URL are retained as a release artifact.

No long-lived Google key is permitted in GitHub Actions.

## Rollback

Run `Roll back production` with a release artifact's immutable digest. The
workflow reapplies the prior image through Terraform and verifies `/health` and
`/readyz`. Do not reverse additive Spanner migrations as part of image rollback.

## Secret rotation

1. Add a new Secret Manager version.
2. Redeploy a new Cloud Run revision through Terraform.
3. Verify the application and provider checks.
4. Disable the previous secret version only after validation.

Never place secret values in Terraform variables, state, commits, artifacts, or
workflow output.

## Domain rollback

If delegation or edge validation fails:

1. Restore the recorded Porkbun nameservers.
2. Confirm the existing Porkbun DNS records are still present.
3. Keep the `run.app` URL active while resolving the issue.
4. Record the incident and next action in the platform report.
