# Hidden Tower Defence — Platform, CI/CD, and Domain Remaining-Work Plan

## Purpose

This plan is for the agent responsible for the remaining work that requires
context from:

- `/agent/repos/Software-Manufacturing-Plant`
- `/agent/repos/chatgraph`

The goal is to finish the production delivery system, edge, domain, rollback,
and observability work while preserving the Software Manufacturing Plant (SMP)
architecture. This agent must not implement application or game features.

This plan is intentionally parallel-safe with
`PRODUCT_GAME_REMAINING_PLAN.md`.

## Current production state

As of 2026-07-19:

- GCP project: `smp-shared-prod`
- Region: `us-central1`
- Cloud Run service: `hiddentowerdefence`
- Public URL:
  `https://hiddentowerdefence-588376054847.us-central1.run.app`
- Verified live routes:
  - `/` → 200
  - `/health` → 200
  - `/readyz` → 200
  - `/api/demo/fixtures` → 200
- `/healthz` is intercepted by Google's edge with a Google-generated 404;
  public probes must use `/health`.
- Cloud Run uses a digest-pinned image.
- Spanner instance: `smp-prod-shared-spanner`
- Spanner database: `hiddentowerdefence`
- Remote Terraform state:
  - Bucket: `smp-substrate-tfstate-prod`
  - Prefix: `products/hiddentowerdefence/prod`
- A remote-state Terraform plan with image digest
  `sha256:0892d9e1c083d97b002ce975c1e31821b62c6067dde4f53ebc456672f02d602a`
  reported no changes.
- Runtime and deployer service accounts exist.
- GitHub WIF pool and provider exist.
- Five runtime Secret Manager resources have populated versions.
- Cloud DNS zone `hiddentowerdefence-com` exists.
- Porkbun remains authoritative; nameserver delegation has not started.
- Existing non-root DNS records were copied manually to Cloud DNS.
- Pull-request validation currently passes both jobs.
- The production GitHub OIDC workflow has not yet been exercised from `main`.

## Authoritative reference files

Read these before editing delivery or infrastructure files.

### Software Manufacturing Plant

- `/agent/repos/Software-Manufacturing-Plant/AGENTS.md`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/_shared-terraform-bootstrap-template.yaml`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/bootstrap-product-infra.yaml`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/flagship-multi-platform-default.yaml`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/promote-release.yaml`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/rollback-release.yaml`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/reconcile-product-domain-bootstrap.yaml`
- `/agent/repos/Software-Manufacturing-Plant/.github/workflows/run-product-domain-bootstrap-retry.yaml`
- `/agent/repos/Software-Manufacturing-Plant/shared-assets/delivery/pipeline-catalog/README.md`
- `/agent/repos/Software-Manufacturing-Plant/shared-assets/delivery/terraform-governance-pack/backend-conventions.yaml`
- `/agent/repos/Software-Manufacturing-Plant/platform/software-manufacturing-plant/implementation-guidebooks/product-launch-golden-path.md`
- `/agent/repos/Software-Manufacturing-Plant/platform/software-manufacturing-plant/implementation-guidebooks/approval-gates-matrix.md`
- `/agent/repos/Software-Manufacturing-Plant/platform/software-manufacturing-plant/implementation-guidebooks/secret-handoff-matrix.md`
- `/agent/repos/Software-Manufacturing-Plant/platform/software-manufacturing-plant/implementation-guidebooks/per-product-secret-input-checklist.md`

### ChatGraph implementation example

- `/agent/repos/chatgraph/.github/workflows/flagship-delivery-prod.yaml`
- `/agent/repos/chatgraph/.github/workflows/validate-factory-scaffold.yaml`
- `/agent/repos/chatgraph/infra/chatgraph-prod-bootstrap/main.tf`
- `/agent/repos/chatgraph/infra/chatgraph-prod-bootstrap/README.md`
- `/agent/repos/chatgraph/docs/runtime-env-contract.md`
- `/agent/repos/chatgraph/docs/shared-secret-wiring.md`
- `/agent/repos/chatgraph/manifests/delivery/pipeline-params/prod.yaml`
- `/agent/repos/chatgraph/reports/launch-validation-summary.yaml`

SMP is authoritative. ChatGraph is a concrete implementation reference, but
its missing automated rollback and smoke gates must not be copied.

## Parallel file ownership

This platform agent owns:

- `infra/**`
- `.github/workflows/**`
- `.gcloudignore`
- Terraform lock files
- New platform/operator documentation under `docs/platform/**`
- New resumable launch reports under `reports/platform/**`

This platform agent must not edit:

- `app/**`
- `tests/**`
- `fixtures/**`
- `app/static/**`
- `pyproject.toml`
- `Dockerfile`
- Product/game documentation owned by the product agent

The application contract that platform code may rely on is:

- Container listens on `PORT`, default `8080`.
- Public liveness endpoint: `/health`.
- Readiness endpoint: `/readyz`.
- Migration entry point: `python -m app.migrations`.
- Required production variables:
  - `environment=production`
  - `database_backend=spanner`
  - `spanner_project_id=smp-shared-prod`
  - `spanner_instance_id=smp-prod-shared-spanner`
  - `spanner_database_id=hiddentowerdefence`
- Required runtime secret environment names:
  - `APIFY_API_TOKEN`
  - `HiddenLayer_API_ClientID`
  - `HiddenLayer_API_ClientSecret`
  - `NVIDIA_nemotron-3-ultra-550b-a55b_API_KEY`
  - `OPERATOR_TOKEN`

If the contract is insufficient, document the required change in
`docs/platform/application-contract-request.md`; do not edit application files.

## Parallel execution protocol

1. Create a dedicated platform branch/worktree from the commit containing both
   remaining-work plans.
2. The product agent creates a separate branch/worktree from the same commit.
3. Do not merge or cherry-pick the product branch while either agent is still
   implementing.
4. Respect the file ownership boundaries above.
5. This platform agent is the only agent allowed to mutate shared production
   GCP, Porkbun, GitHub deployment configuration, or Terraform state.
6. Record any required product contract change under
   `docs/platform/application-contract-request.md`.
7. Consume the product agent's final commit/image only after both agents have
   completed their independent validation.
8. During integration, merge the product branch first, rerun validation, then
   merge/rebase the platform branch and run the production delivery workflow.

This sequencing lets both agents implement concurrently without sharing
mutable files or production control-plane authority.

## Workstream 1 — Reconcile bootstrap drift into Terraform

The initial bootstrap used a small number of direct `gcloud` operations. Make
Terraform and documented bootstrap procedures authoritative.

1. Run a refresh-only plan against the remote state.
2. Inventory project IAM bindings added during bootstrap.
3. Codify required Cloud Build and source-upload permissions in Terraform or a
   dedicated bootstrap root.
4. Remove temporary bootstrap grants that are no longer required.
5. Codify required GCP API enablement.
6. Confirm no temporary Cloud Run services remain.
7. Ensure Terraform prevents accidental deletion of:
   - Spanner database
   - Production Cloud Run service
   - Cloud DNS zone
   - Runtime secrets
8. Run `terraform plan -detailed-exitcode` with the current production digest.
9. Record all intentional out-of-band resources and why they remain external.

Acceptance:

- Remote-state plan is clean.
- No untracked temporary IAM or runtime resources remain.
- Bootstrap credentials are not required for normal deployments.

## Workstream 2 — Codify DNS records and edge infrastructure

The zone exists, but record copies were initially created with `gcloud`.

1. Retrieve the current Porkbun records through its API.
2. Retrieve the current Cloud DNS records.
3. Reconcile required MX, TXT, CNAME, and validation records into Terraform.
4. Import existing records where required instead of recreating them blindly.
5. Do not preserve stale ACME challenge records without confirming they remain
   necessary.
6. Provision through Terraform:
   - Global static IP
   - Serverless NEG targeting the Cloud Run service
   - HTTPS load balancer
   - HTTP-to-HTTPS redirect
   - Certificate Manager certificate and DNS authorization
   - Cloud Armor baseline policy
   - Apex A record
   - `www` record or redirect, if selected
7. Keep the `run.app` URL active throughout edge provisioning.
8. Verify the load balancer by IP/temporary hostname before registrar changes.

Do not delegate the domain until:

- The edge health check passes.
- Apex and mail records exist in Cloud DNS.
- Certificate authorization records exist.
- Existing mail routing has been compared record-for-record.
- Rollback nameservers have been recorded.

## Workstream 3 — Porkbun delegation and durable monitoring

Porkbun API authentication and domain read access are already validated.

1. Record the current authoritative nameservers.
2. Export the complete Porkbun DNS configuration as a resumable artifact.
3. Update Porkbun nameservers to the four Cloud DNS nameservers only after
   Workstream 2 passes.
4. Treat the update as a controlled production mutation.
5. Implement or repair the scheduled domain workflow so it verifies:
   - Public authoritative NS delegation
   - Apex A record
   - MX and SPF continuity
   - Certificate state
   - `https://hiddentowerdefence.com/health`
   - Root page response
6. Make monitoring idempotent and safe to run after readiness.
7. Write a resumable status report containing:
   - Current stage
   - Last successful check
   - Blockers
   - Next action
   - Rollback nameservers

Do not disable the `run.app` URL after domain activation.

## Workstream 4 — Complete the SMP-aligned CI/CD pipeline

### Pull-request validation

The existing validation workflow passes. Extend it to include:

- Locked dependency verification
- Ruff and tests
- Container build
- Container vulnerability threshold
- Terraform formatting and validation
- Infrastructure policy checks
- No apply on pull requests

Pin third-party action revisions according to repository policy.

### Production delivery

The steady-state flow must be:

1. GitHub OIDC authenticates to the dedicated deployer service account.
2. Build the container once.
3. Push it to Artifact Registry.
4. Capture the immutable digest.
5. Run additive/idempotent migrations.
6. Run Terraform plan.
7. Apply Terraform with the exact digest.
8. Smoke `/health`, `/readyz`, `/`, and a read-only application endpoint.
9. Record commit, digest, URL, migration version, and Terraform evidence.

Requirements:

- No long-lived GCP key in GitHub.
- Apply concurrency is serialized.
- Failed smoke checks fail the release.
- The workflow must not use mutable image tags for deployment.
- The workflow must not recreate existing Spanner schema on every deployment.

The product agent owns migration implementation. This platform agent should
verify that the command is idempotent before enabling automatic production
deployments.

### Release metadata and rollback

Add:

- Immutable release metadata artifact.
- Manual rollback workflow accepting a prior digest/release ID.
- Terraform reapply of the prior digest.
- Post-rollback smoke verification.
- Rollback evidence report.

Do not roll back compatible additive schema migrations.

## Workstream 5 — Exercise WIF and production delivery

The WIF provider and service account exist, but the GitHub deployment path is
not yet proven.

1. Verify provider condition restricts:
   - Repository: `huntertcarver/HiddenTowerDefence`
   - Branch: `refs/heads/main`
2. Verify `roles/iam.workloadIdentityUser` binding.
3. Verify deployer permissions are minimal and sufficient.
4. Exercise the production workflow from `main` after the workflow reaches the
   default branch.
5. Confirm no bootstrap credential is used by the workflow.
6. Confirm remote Terraform state locking prevents concurrent mutation.
7. Record the successful GitHub run URL and resulting release digest.

If work remains on a feature branch, validate everything possible and create a
clear post-merge execution checklist; do not weaken the provider condition to
allow arbitrary branches.

## Workstream 6 — Observability and operational readiness

Use SMP observability patterns.

Provision:

- Uptime checks for `/health` and `/readyz`
- Alert for repeated failed heartbeats
- Alert for failed Cloud Run revisions
- Alert for elevated 5xx rates
- Alert for unresolved `LOCKED` incidents, if exposed as a metric
- Log-based metric for provider integration failures
- Dashboard covering Cloud Run, Spanner, provider failures, and releases
- Useful log retention configuration

Investigate and correct noisy Spanner client metric-export warnings rather than
allowing them to obscure application errors.

Create operator documentation for:

- Deployment
- Rollback
- Secret rotation
- Incident response
- Domain rollback
- Spanner migration recovery

## Workstream 7 — Cost and security review

1. Confirm Cloud Run minimum instance and always-allocated CPU are intentional.
2. Confirm the shared Spanner instance remains at 100 processing units unless
   evidence requires scaling.
3. Confirm only one production Cloud Run service exists.
4. Confirm secrets are service-scoped and never exposed to the web client.
5. Confirm the public service exposes only intended unauthenticated routes.
6. Confirm mutating endpoints require application-level authorization.
7. Confirm Cloud Armor policy does not block WebSockets.
8. Confirm no secret values appear in Terraform state or release artifacts.

## Required validation

Run, as applicable:

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform init \
  -backend-config="bucket=smp-substrate-tfstate-prod" \
  -backend-config="prefix=products/hiddentowerdefence/prod"
terraform plan -detailed-exitcode -var="image_digest=<current-digest>"
```

Also verify:

- PR validation workflow succeeds.
- Production OIDC workflow succeeds from `main`.
- Rollback workflow restores a prior digest.
- Edge and domain smoke checks pass.
- Terraform reports no drift after all operations.

## Deliverables

- Terraform-managed production edge and DNS records
- Proven WIF-based production deployment
- Release metadata and rollback workflows
- Domain delegation with resumable monitoring
- Observability resources and runbooks
- Platform launch validation report
- Clean remote Terraform plan

## Definition of done

This platform plan is complete when:

1. No normal deployment requires bootstrap service-account JSON.
2. GitHub OIDC builds once and deploys the captured digest through Terraform.
3. Terraform is authoritative for production runtime, IAM, edge, and DNS.
4. Rollback to a prior digest is tested.
5. `run.app` remains healthy.
6. `hiddentowerdefence.com` serves valid HTTPS without disrupting mail.
7. Domain and certificate monitoring is active and resumable.
8. Alerts and dashboards expose operational failures.
9. A final remote-state plan reports no drift.
