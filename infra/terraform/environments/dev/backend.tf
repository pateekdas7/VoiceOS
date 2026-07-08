# dev environment -- local state backend (Sprint-026, Phase 1)
#
# Deliberate deviation from IaC-3's "state stored in remote backend" for
# staging/production: dev exists specifically for fast local `terraform
# validate`/`terraform plan` iteration during Phase 1, without requiring a
# real AWS account or S3/DynamoDB bootstrap. staging/production (the actual
# CI/CD-applied, "no manual prod changes" environments IaC-3 is protecting)
# use the real S3+DynamoDB remote backend -- see their backend.tf files.

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
