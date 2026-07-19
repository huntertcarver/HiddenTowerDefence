variable "project_id" {
  type    = string
  default = "smp-shared-prod"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "terraform_state_bucket" {
  type        = string
  default     = "smp-substrate-tfstate-prod"
  description = "Existing bucket used by the GCS backend for product Terraform state."
}

variable "image_digest" {
  type        = string
  description = "Immutable Artifact Registry image reference including @sha256 digest."
}

variable "github_repository" {
  type    = string
  default = "huntertcarver/HiddenTowerDefence"
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "spanner_instance_id" {
  type    = string
  default = "smp-prod-shared-spanner"
}

variable "spanner_database_id" {
  type    = string
  default = "hiddentowerdefence"
}

variable "secret_ids" {
  type = set(string)
  default = [
    "secret--hiddentowerdefence--prod--apify-api-token",
    "secret--hiddentowerdefence--prod--hiddenlayer-client-id",
    "secret--hiddentowerdefence--prod--hiddenlayer-client-secret",
    "secret--hiddentowerdefence--prod--nvidia-api-key",
    "secret--hiddentowerdefence--prod--operator-token",
  ]
}

variable "domain_name" {
  type    = string
  default = "hiddentowerdefence.com."
}
