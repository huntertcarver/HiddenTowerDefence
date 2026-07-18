provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" {}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "hiddentower-runtime-prod"
  display_name = "Hidden Tower Defence production runtime"
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "hiddentower-deployer-prod"
  display_name = "Hidden Tower Defence GitHub Actions deployer"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "hiddentower-github"
  display_name              = "Hidden Tower GitHub Actions"
  description               = "OIDC federation for the HiddenTowerDefence main branch."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "Hidden Tower GitHub Actions provider"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.ref == 'refs/heads/${var.github_branch}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_spanner_database" "application" {
  instance = var.spanner_instance_id
  name     = var.spanner_database_id

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_dns_managed_zone" "application" {
  project     = var.project_id
  name        = "hiddentowerdefence-com"
  dns_name    = var.domain_name
  description = "Authoritative DNS zone. Delegate only after importing existing DNS records."
}

resource "google_secret_manager_secret" "runtime" {
  for_each  = var.secret_ids
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  for_each  = google_secret_manager_secret.runtime
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_spanner" {
  project = var.project_id
  role    = "roles/spanner.databaseUser"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_secret_admin" {
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_spanner" {
  project = var.project_id
  role    = "roles/spanner.databaseUser"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_runtime_impersonation" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_cloud_run_v2_service" "application" {
  project  = var.project_id
  location = var.region
  name     = "hiddentowerdefence"

  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email
    timeout         = "900s"

    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = var.image_digest

      resources {
        cpu_idle = false
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "environment"
        value = "production"
      }
      env {
        name  = "database_backend"
        value = "spanner"
      }
      env {
        name  = "spanner_project_id"
        value = var.project_id
      }
      env {
        name  = "spanner_instance_id"
        value = var.spanner_instance_id
      }
      env {
        name  = "spanner_database_id"
        value = var.spanner_database_id
      }

      dynamic "env" {
        for_each = {
          APIFY_API_TOKEN = google_secret_manager_secret.runtime["secret--hiddentowerdefence--prod--apify-api-token"].secret_id
          HiddenLayer_API_ClientID = google_secret_manager_secret.runtime["secret--hiddentowerdefence--prod--hiddenlayer-client-id"].secret_id
          HiddenLayer_API_ClientSecret = google_secret_manager_secret.runtime["secret--hiddentowerdefence--prod--hiddenlayer-client-secret"].secret_id
          NVIDIA_nemotron-3-ultra-550b-a55b_API_KEY = google_secret_manager_secret.runtime["secret--hiddentowerdefence--prod--nvidia-api-key"].secret_id
          OPERATOR_TOKEN = google_secret_manager_secret.runtime["secret--hiddentowerdefence--prod--operator-token"].secret_id
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  traffic {
    percent = 100
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
  }

  depends_on = [
    google_secret_manager_secret_iam_member.runtime_accessor,
    google_project_iam_member.runtime_spanner,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.application.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "service_uri" {
  value = google_cloud_run_v2_service.application.uri
}

output "deployer_service_account_email" {
  value = google_service_account.deployer.email
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "cloud_dns_nameservers" {
  value = google_dns_managed_zone.application.name_servers
}
