locals {
  is_arm = startswith(var.instance_type, "t4g.")
  ami_ssm_parameter = format(
    "/aws/service/canonical/ubuntu/server/24.04/stable/current/%s/hvm/ebs-gp3/ami-id",
    local.is_arm ? "arm64" : "amd64"
  )
  public_ip = aws_eip.this.public_ip
  hostname  = var.domain != "" ? var.domain : "${replace(local.public_ip, ".", "-")}.sslip.io"
}

# ---------------------------------------------------------------------------------------------------------------------
# Network: the account's default VPC (no NAT gateway, no extra subnets, no load balancer = nothing that bills by the hour)
# ---------------------------------------------------------------------------------------------------------------------
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Pick a subnet in an AZ that actually offers the chosen instance type.
data "aws_ec2_instance_type_offerings" "this" {
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
  location_type = "availability-zone"
}

data "aws_subnet" "candidates" {
  for_each = toset(data.aws_subnets.default.ids)
  id       = each.value
}

locals {
  usable_subnets = sort([
    for id, s in data.aws_subnet.candidates : id
    if contains(data.aws_ec2_instance_type_offerings.this.locations, s.availability_zone)
  ])
}

data "aws_ssm_parameter" "ubuntu" {
  name = local.ami_ssm_parameter
}

# ---------------------------------------------------------------------------------------------------------------------
# Security group: HTTPS (and HTTP for the certificate challenge + redirect). SSH only if you give a CIDR.
# ---------------------------------------------------------------------------------------------------------------------
resource "aws_security_group" "this" {
  name        = "${var.name}-sg"
  description = "Agentic Job Seeker: 80/443 only"
  vpc_id      = data.aws_vpc.default.id

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.this.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "http3" {
  security_group_id = aws_security_group.this.id
  description       = "HTTP/3 (QUIC)"
  ip_protocol       = "udp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.this.id
  description       = "HTTP (ACME challenge + redirect to HTTPS)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  count             = var.ssh_cidr == "" ? 0 : 1
  security_group_id = aws_security_group.this.id
  description       = "SSH from a single trusted CIDR"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.ssh_cidr
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  description       = "Outbound (job boards, Gmail SMTP, package repos)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------------------------------------------------------------------------------------------------------------------
# IAM: the instance can only be managed through SSM (Session Manager + Run Command used by the Deploy workflow)
# ---------------------------------------------------------------------------------------------------------------------
data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.name
}

# ---------------------------------------------------------------------------------------------------------------------
# Compute: one EC2 instance, Elastic IP, encrypted gp3 root volume
# ---------------------------------------------------------------------------------------------------------------------
resource "aws_eip" "this" {
  domain = "vpc"
  tags   = { Name = var.name }
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.ubuntu.insecure_value
  instance_type          = var.instance_type
  subnet_id              = local.usable_subnets[0]
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = aws_iam_instance_profile.this.name

  # "standard" credits: a busy burstable instance is throttled instead of billed for T3 "unlimited" surplus credits.
  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # containers on the host can still reach IMDS if ever needed
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    public_ip  = local.public_ip
    region     = var.region
    ssm_prefix = local.ssm_prefix
    repo_url   = var.repo_url
    repo_ref   = var.repo_ref
  })
  user_data_replace_on_change = false # never rebuild a live server just because the bootstrap script changed

  tags = { Name = var.name }

  # The first boot reads these parameters and writes generated secrets, so they must exist before the instance starts.
  depends_on = [
    aws_ssm_parameter.secret,
    aws_ssm_parameter.env,
    aws_iam_role_policy.parameters,
    aws_iam_role_policy_attachment.ssm,
  ]

  lifecycle {
    ignore_changes = [ami, user_data] # AMI updates / re-bootstraps are deliberate: taint the instance to apply them
  }
}

resource "aws_eip_association" "this" {
  instance_id   = aws_instance.this.id
  allocation_id = aws_eip.this.id
}

# ---------------------------------------------------------------------------------------------------------------------
# Cost guard
# ---------------------------------------------------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}
