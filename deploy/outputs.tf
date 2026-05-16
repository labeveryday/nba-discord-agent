output "instance_ip" {
  description = "Public IPv4 address of the instance"
  value       = tolist(linode_instance.this.ipv4)[0]
}

output "instance_id" {
  description = "Linode instance ID"
  value       = linode_instance.this.id
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -p ${var.ssh_port} ${var.ssh_user}@${tolist(linode_instance.this.ipv4)[0]}"
}

output "deploy_steps" {
  description = "Commands to run after SSH-ing in"
  value       = <<-EOT

    # Wait ~3 minutes for cloud-init to finish, then:
    ssh -p ${var.ssh_port} ${var.ssh_user}@${tolist(linode_instance.this.ipv4)[0]}

    # Deploy the bot:
    git clone https://github.com/labeveryday/nba-discord-agent.git
    cd nba-discord-agent
    cp env.example .env
    nano .env   # Set DISCORD_TOKEN, MODEL_PROVIDER=anthropic, ANTHROPIC_API_KEY
    chmod 600 .env
    docker compose up -d --build

  EOT
}

output "allowed_ips" {
  description = "IP addresses allowed through the firewall"
  value       = local.allowed_ips
  sensitive   = true
}

output "firewall_id" {
  description = "Cloud Firewall ID"
  value       = linode_firewall.this.id
}
