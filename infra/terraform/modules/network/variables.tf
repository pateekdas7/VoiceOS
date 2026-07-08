variable "name_prefix" {
  description = "Prefix for all resource names (e.g. 'voiceos-dev')."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "availability_zones" {
  description = "Availability zones to spread subnets across."
  type        = list(string)
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
  default     = {}
}
