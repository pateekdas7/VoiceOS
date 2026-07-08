variable "name_prefix" {
  type = string
}

variable "service_names" {
  type = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
