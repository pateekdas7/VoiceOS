# production environment -- remote state backend (IaC-3: S3 + DynamoDB state lock)
#
# IaC-3 enforcement: this environment is applied via CI/CD only. A manual
# `terraform apply` from a developer workstation is not blocked by Terraform
# itself (no such native control exists) -- it is enforced organizationally
# by restricting who holds credentials capable of assuming the CI/CD apply
# role, documented in .github/workflows/release.yml's `terraform-apply` job
# (manual `workflow_dispatch` gated by required reviewers, never on every push).

terraform {
  backend "s3" {
    bucket         = "voiceos-terraform-state"
    key            = "production/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "voiceos-terraform-locks"
    encrypt        = true
  }
}
