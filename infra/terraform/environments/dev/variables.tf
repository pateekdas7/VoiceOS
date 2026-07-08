variable "environment" {
  type    = string
  default = "dev"
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
  description = "Ubuntu 22.04 AMI ID for ap-south-1 -- placeholder for offline Phase 1 plan/validate; refresh before a real apply (see modules/mongodb/variables.tf)."
  type        = string
  default     = "ami-0dev00000000dev0" # NOT a real AMI id -- dev never actually applies without refreshing this.
}
