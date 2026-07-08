# dev environment -- small, single-AZ-tolerant sizing for fast Phase 1 iteration.

module "voiceos" {
  source = "../../"

  environment = var.environment
  region      = var.region
  tags        = var.tags

  availability_zones = ["ap-south-1a", "ap-south-1b"]

  cpu_node_instance_type    = "t3.large"
  gpu_node_instance_type    = "g5.xlarge"
  data_node_instance_type   = "t3.medium"
  system_node_instance_type = "t3.medium"

  cpu_node_desired_count    = 1
  gpu_node_desired_count    = 0 # dev has no GPU workload -- keep the (expensive) GPU node pool scaled to zero.
  data_node_desired_count   = 1 # single-node "replica set" for dev; staging/production use 3.
  system_node_desired_count = 1

  postgres_instance_class       = "db.t3.medium"
  postgres_multi_az             = false
  postgres_allocated_storage_gb = 20

  redis_node_type = "cache.t3.small"
  redis_multi_az  = false

  mongodb_ami_id = var.mongodb_ami_id
}
