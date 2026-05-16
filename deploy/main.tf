provider "linode" {
  token = var.linode_token
}

# ---------------------------------------------------------------------------
# Auto-detect your public IP if var.allowed_ip is not set
# ---------------------------------------------------------------------------
# data "http" "my_ip" {
#   url = "https://api.ipify.org"
# }

locals {
  # Always include the current IP (where you're running terraform from),
  # plus any additional IPs from var.allowed_ips (e.g. work computer)
  # my_ip       = "${chomp(data.http.my_ip.response_body)}/32"
  allowed_ips = var.allowed_ips

  # Bare IPs (without /32) for UFW rules inside cloud-init
  allowed_ips_bare = [for ip in local.allowed_ips : split("/", ip)[0]]

  cloud_init = templatefile("${path.module}/cloud-init.yaml", {
    ssh_user         = var.ssh_user
    ssh_port         = var.ssh_port
    allowed_ips_bare = local.allowed_ips_bare
  })
}

# ---------------------------------------------------------------------------
# Compute instance
# ---------------------------------------------------------------------------
resource "linode_instance" "this" {
  label  = var.label
  region = var.region
  type   = var.instance_type
  image  = var.image
  tags   = var.tags

  root_pass       = var.root_pass
  authorized_keys = var.authorized_keys

  metadata {
    user_data = base64encode(local.cloud_init)
  }
}

# ---------------------------------------------------------------------------
# Cloud Firewall — SSH inbound from your IP only, restricted outbound
# ---------------------------------------------------------------------------
resource "linode_firewall" "this" {
  label = "${var.label}-fw"
  tags  = var.tags

  # --- Inbound rules ---

  inbound {
    label    = "allow-ssh-ipv4"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = tostring(var.ssh_port)
    ipv4     = local.allowed_ips
  }

  inbound {
    label    = "allow-icmp-ipv4"
    action   = "ACCEPT"
    protocol = "ICMP"
    ipv4     = local.allowed_ips
  }

  inbound_policy = "DROP"

  # --- Outbound rules (DNS, NTP, HTTP, HTTPS — all the bot needs) ---

  outbound {
    label    = "allow-dns-tcp"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "53"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  outbound {
    label    = "allow-dns-udp"
    action   = "ACCEPT"
    protocol = "UDP"
    ports    = "53"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  outbound {
    label    = "allow-ntp"
    action   = "ACCEPT"
    protocol = "UDP"
    ports    = "123"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  outbound {
    label    = "allow-http"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "80"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  outbound {
    label    = "allow-https"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "443"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  outbound_policy = "DROP"

  linodes = [linode_instance.this.id]
}
