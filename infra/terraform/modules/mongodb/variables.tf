variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "allowed_security_group_ids" {
  type = list(string)
}

variable "instance_type" {
  type = string
}

variable "ami_id" {
  description = <<-EOT
    Ubuntu 22.04 AMI ID for the region being deployed to. Pinned explicitly
    (not resolved via an `aws_ami` data source) for two reasons: (1) a data
    source re-resolves "most recent" on every plan, silently proposing a
    node replacement whenever Canonical publishes a new build -- an explicit
    pin makes AMI upgrades a deliberate, reviewed change instead; (2) a data
    source requires a live DescribeImages API call even during `terraform
    plan`, which breaks the credential-free dev-environment plan this
    module tree is designed to support (found running a real `terraform
    plan` against dev with no AWS account, not assumed -- see CHANGELOG.md's
    Sprint-026 entry). Refresh via:
      aws ssm get-parameter --region <region> \
        --name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
        --query 'Parameter.Value' --output text
  EOT
  type        = string
}

variable "replica_count" {
  description = "MongoDB replica set member count (3 = one primary + two secondaries, standard PSS topology)."
  type        = number
  default     = 3
}

variable "tags" {
  type    = map(string)
  default = {}
}
