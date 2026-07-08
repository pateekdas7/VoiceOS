# Shared provider version constraint (Sprint-026, IaC-3: one module tree).
# No provider *configuration* block here -- see each environments/<env>/provider.tf.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
