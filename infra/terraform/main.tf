# VoiceOS v2 -- Shared Terraform module tree (Sprint-026, V7 Ch2/Ch3/Ch5)
#
# This directory is a *reusable module*, not a runnable root configuration --
# `terraform` block, `provider` block, and the remote-state `backend` all
# live in each environments/<env>/ directory instead (a provider/backend
# belongs to the root that's actually run, not to a module three
# environments share). See environments/dev/main.tf for how this module is
# instantiated; versions.tf declares the provider version constraint shared
# by every environment.
#
# Composes every infrastructure module into one environment. Environment
# differences (instance sizes, Multi-AZ, node counts) come entirely from
# variables -- this file is identical across dev/staging/production
# (IaC-3: one module tree, environment-specific inputs only).

locals {
  name_prefix = "voiceos-${var.environment}"
}

module "network" {
  source = "./modules/network"

  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  tags               = var.tags
}

module "kms" {
  source = "./modules/kms"

  name_prefix = local.name_prefix
  tags        = var.tags
}

module "kubernetes" {
  source = "./modules/kubernetes"

  name_prefix               = local.name_prefix
  cluster_name              = "${local.name_prefix}-${var.cluster_name}"
  kubernetes_version        = var.kubernetes_version
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_kms_key_arn       = module.kms.system_key_arn
  cpu_node_instance_type    = var.cpu_node_instance_type
  gpu_node_instance_type    = var.gpu_node_instance_type
  data_node_instance_type   = var.data_node_instance_type
  system_node_instance_type = var.system_node_instance_type
  cpu_node_desired_count    = var.cpu_node_desired_count
  gpu_node_desired_count    = var.gpu_node_desired_count
  data_node_desired_count   = var.data_node_desired_count
  system_node_desired_count = var.system_node_desired_count
  tags                      = var.tags
}

module "database" {
  source = "./modules/database"

  name_prefix                = local.name_prefix
  vpc_id                     = module.network.vpc_id
  private_subnet_ids         = module.network.private_subnet_ids
  allowed_security_group_ids = [module.kubernetes.node_security_group_id]
  instance_class             = var.postgres_instance_class
  multi_az                   = var.postgres_multi_az
  allocated_storage_gb       = var.postgres_allocated_storage_gb
  kms_key_arn                = module.kms.system_key_arn
  tags                       = var.tags
}

module "redis" {
  source = "./modules/redis"

  name_prefix                = local.name_prefix
  vpc_id                     = module.network.vpc_id
  private_subnet_ids         = module.network.private_subnet_ids
  allowed_security_group_ids = [module.kubernetes.node_security_group_id]
  node_type                  = var.redis_node_type
  multi_az                   = var.redis_multi_az
  tags                       = var.tags
}

module "mongodb" {
  source = "./modules/mongodb"

  name_prefix                = local.name_prefix
  vpc_id                     = module.network.vpc_id
  private_subnet_ids         = module.network.private_subnet_ids
  allowed_security_group_ids = [module.kubernetes.node_security_group_id]
  instance_type              = var.data_node_instance_type
  replica_count              = var.data_node_desired_count
  ami_id                     = var.mongodb_ami_id
  tags                       = var.tags
}

module "object_storage" {
  source = "./modules/object-storage"

  name_prefix = local.name_prefix
  kms_key_arn = module.kms.system_key_arn
  tags        = var.tags
}

module "registry" {
  source = "./modules/registry"

  name_prefix   = local.name_prefix
  service_names = var.service_names
  tags          = var.tags
}
