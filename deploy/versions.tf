terraform {
  required_version = ">= 1.5"

  required_providers {
    linode = {
      source  = "linode/linode"
      version = "~> 2.41"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.5"
    }
  }
}
