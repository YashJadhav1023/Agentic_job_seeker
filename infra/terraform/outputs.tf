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

output "ssm_prefix" {
  description = "Parameter Store path holding all secrets (<prefix>/secret/*) and config (<prefix>/env/*)"
  value       = local.ssm_prefix
}

output "get_api_token" {
  description = "Read the app's API token from Parameter Store (needs AWS credentials with ssm:GetParameter on the prefix)"
  value       = "aws ssm get-parameter --region ${var.region} --name ${local.ssm_prefix}/secret/JOBHUB_API_TOKEN --with-decryption --query Parameter.Value --output text"
}

output "shell_access" {
  description = "Shell on the instance without SSH"
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.this.id}"
}
