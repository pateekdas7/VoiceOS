# dev environment provider config -- runs Phase 1 `terraform validate`/`plan`
# with no real AWS account (Sprint-026.md: "No CPU or GPU infrastructure is
# required for this phase"). The skip_* flags below stop the AWS provider
# from making any STS/credential API call during init/plan, matching the
# well-known "offline plan" pattern for CI validation without live cloud
# credentials. staging/production do not set these -- they run against a
# real AWS account, applied via CI/CD only per IaC-3.

provider "aws" {
  region = var.region

  access_key                  = "dev-placeholder-access-key"
  secret_key                  = "dev-placeholder-secret-key"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  default_tags {
    tags = merge(var.tags, { Environment = var.environment })
  }
}
