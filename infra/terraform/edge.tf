resource "google_compute_global_address" "edge" {
  project      = var.project_id
  name         = "hiddentowerdefence-edge-ip"
  address_type = "EXTERNAL"
  ip_version   = "IPV4"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_region_network_endpoint_group" "cloud_run" {
  project               = var.project_id
  region                = var.region
  name                  = "hiddentowerdefence-cloud-run-neg"
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.application.name
  }
}

resource "google_compute_security_policy" "edge" {
  project = var.project_id
  name    = "hiddentowerdefence-edge"

  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Allow public game and API traffic; application authentication protects mutations."
  }
}

resource "google_compute_backend_service" "edge" {
  project               = var.project_id
  name                  = "hiddentowerdefence-edge-backend"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.edge.id

  backend {
    group = google_compute_region_network_endpoint_group.cloud_run.id
  }
}

resource "google_compute_url_map" "https" {
  project         = var.project_id
  name            = "hiddentowerdefence-https"
  default_service = google_compute_backend_service.edge.id
}

resource "google_certificate_manager_dns_authorization" "domain" {
  project = var.project_id
  name    = "hiddentowerdefence-com"
  domain  = trimsuffix(var.domain_name, ".")
}

resource "google_certificate_manager_certificate" "domain" {
  project = var.project_id
  name    = "hiddentowerdefence-com"

  managed {
    domains            = [trimsuffix(var.domain_name, ".")]
    dns_authorizations = [google_certificate_manager_dns_authorization.domain.id]
  }
}

resource "google_certificate_manager_certificate_map" "edge" {
  project = var.project_id
  name    = "hiddentowerdefence-edge"
}

resource "google_certificate_manager_certificate_map_entry" "domain" {
  project      = var.project_id
  name         = "hiddentowerdefence-com"
  map          = google_certificate_manager_certificate_map.edge.name
  certificates = [google_certificate_manager_certificate.domain.id]
  hostname     = trimsuffix(var.domain_name, ".")
}

resource "google_dns_record_set" "certificate_authorization" {
  managed_zone = google_dns_managed_zone.application.name
  name         = google_certificate_manager_dns_authorization.domain.dns_resource_record[0].name
  type         = google_certificate_manager_dns_authorization.domain.dns_resource_record[0].type
  ttl          = 300
  rrdatas      = [google_certificate_manager_dns_authorization.domain.dns_resource_record[0].data]
}

resource "google_compute_target_https_proxy" "edge" {
  project         = var.project_id
  name            = "hiddentowerdefence-https"
  url_map         = google_compute_url_map.https.id
  certificate_map = "//certificatemanager.googleapis.com/${google_certificate_manager_certificate_map.edge.id}"
}

resource "google_compute_global_forwarding_rule" "https" {
  project               = var.project_id
  name                  = "hiddentowerdefence-https"
  ip_address            = google_compute_global_address.edge.id
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  target                = google_compute_target_https_proxy.edge.id
}

resource "google_compute_url_map" "http_redirect" {
  project = var.project_id
  name    = "hiddentowerdefence-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "http_redirect" {
  project = var.project_id
  name    = "hiddentowerdefence-http"
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  project               = var.project_id
  name                  = "hiddentowerdefence-http"
  ip_address            = google_compute_global_address.edge.id
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "80"
  target                = google_compute_target_http_proxy.http_redirect.id
}

resource "google_dns_record_set" "apex" {
  managed_zone = google_dns_managed_zone.application.name
  name         = var.domain_name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.edge.address]
}

resource "google_dns_record_set" "mail_mx" {
  managed_zone = google_dns_managed_zone.application.name
  name         = var.domain_name
  type         = "MX"
  ttl          = 600
  rrdatas = [
    "10 fwd1.porkbun.com.",
    "20 fwd2.porkbun.com.",
  ]
}

resource "google_dns_record_set" "spf" {
  managed_zone = google_dns_managed_zone.application.name
  name         = var.domain_name
  type         = "TXT"
  ttl          = 600
  rrdatas      = ["\"v=spf1 include:_spf.porkbun.com ~all\""]
}

resource "google_dns_record_set" "wildcard" {
  managed_zone = google_dns_managed_zone.application.name
  name         = "*.${var.domain_name}"
  type         = "CNAME"
  ttl          = 600
  rrdatas      = ["uixie.porkbun.com."]
}

output "edge_ip_address" {
  value = google_compute_global_address.edge.address
}

output "certificate_dns_authorization" {
  value = google_certificate_manager_dns_authorization.domain.dns_resource_record
}
