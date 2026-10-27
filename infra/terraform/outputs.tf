output "server_url" {
  description = "Put this in the Android app (Settings > Server URL)"
  value       = "https://${local.hostname}"
}

output "public_ip" {
  value = aws_eip.this.public_ip
}

output "instance_id" {
  value = aws_instance.this.id
}

output "get_credentials" {
  description = "Prints the API token + remote-browser login once (created by setup.sh on first boot)"
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.this.id}   # then: sudo cat /root/ajs-credentials.txt"
}
