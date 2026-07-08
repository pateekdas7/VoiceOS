variable "environment" {
  type    = string
  default = "staging"
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "tags" {
  type = map(string)
  default = {
    Project   = "voiceos-v2"
    ManagedBy = "terraform"
  }
}

variable "mongodb_ami_id" {
  description = "Ubuntu 22.04 AMI ID for ap-south-1. Refresh before every apply (see modules/mongodb/variables.tf's refresh command) -- no default on purpose, this environment is applied for real."
  type        = string
}
