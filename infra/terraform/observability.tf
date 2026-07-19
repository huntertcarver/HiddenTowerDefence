resource "google_logging_project_bucket_config" "application" {
  project        = var.project_id
  location       = "global"
  bucket_id      = "hiddentowerdefence-application"
  retention_days = 30
}

resource "google_logging_metric" "provider_failures" {
  project = var.project_id
  name    = "hiddentowerdefence_provider_failures"
  filter  = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="hiddentowerdefence"
    (
      textPayload:"Apify ingestion failed" OR
      textPayload:"HiddenLayer" OR
      textPayload:"Nemotron"
    )
  EOT
}

resource "google_monitoring_uptime_check_config" "health" {
  project      = var.project_id
  display_name = "Hidden Tower Defence public health"
  timeout      = "10s"
  period       = "60s"

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = replace(google_cloud_run_v2_service.application.uri, "https://", "")
    }
  }

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
}

resource "google_monitoring_alert_policy" "health" {
  project      = var.project_id
  display_name = "Hidden Tower Defence health check failure"
  combiner     = "OR"

  conditions {
    display_name = "Public health check failed"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_NEXT_OLDER"
      }
    }
  }
}

resource "google_monitoring_dashboard" "application" {
  project = var.project_id
  dashboard_json = jsonencode({
    displayName = "Hidden Tower Defence production"
    mosaicLayout = {
      columns = 12
      tiles = [
        {
          width  = 6
          height = 4
          widget = {
            title = "Cloud Run request count"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_count\" resource.type=\"cloud_run_revision\" resource.labels.service_name=\"hiddentowerdefence\""
                  }
                }
              }]
            }
          }
        },
        {
          xPos   = 6
          width  = 6
          height = 4
          widget = {
            title = "Provider failures"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/hiddentowerdefence_provider_failures\""
                  }
                }
              }]
            }
          }
        }
      ]
    }
  })
}
