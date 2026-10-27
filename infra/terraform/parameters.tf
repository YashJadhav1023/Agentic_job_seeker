locals {
  ssm_prefix = "/${var.name}"

  # Secrets: created EMPTY ("UNSET") by Terraform so no secret value ever lands in code or Terraform state.
  # The first three are generated on the instance at first boot (deploy/bootstrap-env.sh); the Gmail app password is
  # yours to set once (see README). Terraform ignores later value changes.
  secret_names = [
    "JOBHUB_API_TOKEN",
    "JOBHUB_VNC_PASSWORD",
    "JOBHUB_BROWSER_PASSWORD",
    "JOBHUB_BROWSER_HASH",
    "JOBHUB_SECRET_GMAIL_APP_PASSWORD",
  ]

  # Plain configuration (not secret). Every String parameter under <prefix>/env becomes an environment variable.
  env_values = {
    JOBHUB_DOMAIN       = local.hostname
    JOBHUB_BROWSER_USER = "jobhub"
    JOBHUB_DISABLE_DOCS = "1"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_ssm_parameter" "secret" {
  for_each    = toset(local.secret_names)
  name        = "${local.ssm_prefix}/secret/${each.value}"
  description = "Agentic Job Seeker secret (value managed outside Terraform)"
  type        = "SecureString" # default aws/ssm AWS-managed key: no KMS key to pay for
  tier        = "Standard"
  value       = "UNSET"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "env" {
  for_each    = local.env_values
  name        = "${local.ssm_prefix}/env/${each.key}"
  description = "Agentic Job Seeker configuration"
  type        = "String"
  tier        = "Standard"
  value       = each.value
}

# The instance may read everything under its prefix and write only generated secrets.
data "aws_iam_policy_document" "parameters" {
  statement {
    sid       = "ReadConfig"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*"]
  }
  statement {
    sid       = "WriteGeneratedSecrets"
    actions   = ["ssm:PutParameter"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/secret/*"]
  }
}

resource "aws_iam_role_policy" "parameters" {
  name   = "${var.name}-parameters"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.parameters.json
}
