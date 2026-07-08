variable "name_prefix" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "kubernetes_version" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "cluster_kms_key_arn" {
  description = "KMS key ARN used to encrypt EKS secrets (envelope encryption at the control-plane level)."
  type        = string
}

variable "cpu_node_instance_type" {
  type = string
}

variable "gpu_node_instance_type" {
  type = string
}

variable "data_node_instance_type" {
  type = string
}

variable "system_node_instance_type" {
  type = string
}

variable "cpu_node_desired_count" {
  type = number
}

variable "gpu_node_desired_count" {
  type = number
}

variable "data_node_desired_count" {
  type = number
}

variable "system_node_desired_count" {
  type = number
}

variable "tags" {
  type    = map(string)
  default = {}
}
