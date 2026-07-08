# staging environment -- production-shaped topology (Multi-AZ) at reduced
# scale, so it actually exercises the failover paths production relies on.

module "voiceos" {
  source = "../../"

  environment = var.environment
  region      = var.region
  tags        = var.tags

  availability_zones = ["ap-south-1a", "ap-south-1b"]

  cpu_node_instance_type    = "m6i.large"
  gpu_node_instance_type    = "g5.2xlarge"
  data_node_instance_type   = "m6i.large"
  system_node_instance_type = "m6i.large"

  cpu_node_desired_count    = 2
  gpu_node_desired_count    = 1
  data_node_desired_count   = 3
  system_node_desired_count = 2

  postgres_instance_class       = "db.r6g.large"
  postgres_multi_az             = true
  postgres_allocated_storage_gb = 100

  redis_node_type = "cache.r6g.large"
  redis_multi_az  = true

  mongodb_ami_id = var.mongodb_ami_id
}
