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
  description = "Security groups permitted to reach Postgres on 5432 (deny-all default + explicit allow-list)."
  type        = list(string)
}

variable "instance_class" {
  type = string
}

variable "multi_az" {
  type = bool
}

variable "allocated_storage_gb" {
  type = number
}

variable "kms_key_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
