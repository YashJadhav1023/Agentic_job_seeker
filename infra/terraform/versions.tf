terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Remote state in S3 with native lockfile locking (no DynamoDB table needed).
  # Bucket/key/region are supplied by the pipeline: terraform init -backend-config=...
  # (see .github/workflows/terraform.yml). Locally: terraform init -backend=false
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.name
      ManagedBy = "terraform"
      Repo      = "YashJadhav1023/Agentic_job_seeker"
    }
  }
}
