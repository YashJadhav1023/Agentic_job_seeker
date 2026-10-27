variable "region" {
  description = "AWS region. ap-south-1 (Mumbai) is closest to the Indian job boards."
  type        = string
  default     = "ap-south-1"
}

variable "name" {
  description = "Name prefix for every resource (also the Project tag the deploy workflow uses to find the instance)."
  type        = string
  default     = "agentic-job-seeker"
}

variable "instance_type" {
  description = "t4g.small (ARM, 2 GB) is the best value. x86 alternatives: t3.small / t3.micro."
  type        = string
  default     = "t4g.small"

  validation {
    condition     = contains(["t4g.micro", "t4g.small", "t3.micro", "t3.small"], var.instance_type)
    error_message = "instance_type must be one of t4g.micro, t4g.small, t3.micro, t3.small."
  }
}

variable "root_volume_gb" {
  description = "Root EBS volume size (gp3, encrypted). Holds Docker images, the Chromium cache and run logs."
  type        = number
  default     = 30

  validation {
    condition     = var.root_volume_gb >= 20 && var.root_volume_gb <= 100
    error_message = "root_volume_gb must be between 20 and 100."
  }
}

variable "domain" {
  description = "Optional DNS name you own, pointing at the Elastic IP. Empty = <ip-with-dashes>.sslip.io (free, real certificate)."
  type        = string
  default     = ""
}

variable "repo_url" {
  description = "Git repository the instance clones on first boot."
  type        = string
  default     = "https://github.com/YashJadhav1023/Agentic_job_seeker.git"
}

variable "repo_ref" {
  description = "Branch or tag cloned on first boot (later deploys are done by the Deploy workflow)."
  type        = string
  default     = "main"
}

variable "budget_email" {
  description = "Email for the cost alarm. Alerts when any real charge is forecast (limit: 1 USD/month)."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_email))
    error_message = "budget_email must be a valid email address."
  }
}

variable "budget_limit_usd" {
  description = "Monthly budget threshold in USD."
  type        = number
  default     = 20
}

variable "ssh_cidr" {
  description = "Optional CIDR allowed to SSH (e.g. 203.0.113.4/32). Empty = port 22 closed; use SSM Session Manager."
  type        = string
  default     = ""
}
