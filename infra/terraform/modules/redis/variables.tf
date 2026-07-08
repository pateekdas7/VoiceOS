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

variable "node_type" {
  type = string
}

variable "multi_az" {
  type = bool
}

variable "tags" {
  type    = map(string)
  default = {}
}
