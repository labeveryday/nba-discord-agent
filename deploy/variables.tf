variable "linode_token" {
  description = "Linode API token"
  type        = string
  sensitive   = true
}

variable "root_pass" {
  description = "Root password for the instance (required by Linode, but SSH key auth is enforced)"
  type        = string
  sensitive   = true
}

variable "authorized_keys" {
  description = "List of SSH public keys to authorize"
  type        = list(string)
}

variable "allowed_ips" {
  description = "Additional IP addresses allowed to access this instance (CIDR notation, e.g. [\"203.0.113.10/32\"]). Your current IP is always included automatically."
  type        = list(string)
  default     = []
}

variable "region" {
  description = "Linode region"
  type        = string
  default     = "us-east"
}

variable "instance_type" {
  description = "Linode instance type — g6-nanode-1 (2GB, $12/mo) is enough for Anthropic backend"
  type        = string
  default     = "g6-nanode-1"
}

variable "image" {
  description = "Linode image to deploy"
  type        = string
  default     = "linode/ubuntu24.04"
}

variable "label" {
  description = "Label for the instance and related resources"
  type        = string
  default     = "nba-discord-agent"
}

variable "ssh_user" {
  description = "Non-root sudo user to create via cloud-init"
  type        = string
  default     = "deploy"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_user))
    error_message = "Must be a valid Unix username (lowercase, starts with letter or underscore, max 32 chars)."
  }
}

variable "ssh_port" {
  description = "SSH port (change to reduce log noise from scanners)"
  type        = number
  default     = 22
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = list(string)
  default     = ["nba-agent", "terraform"]
}
