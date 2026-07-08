# VoiceOS v2 -- Root Terraform variables (Sprint-026, V7 Ch2/Ch3/Ch5)
#
# Every environment (dev/staging/production) supplies its own values for
# these via environments/<env>/terraform.tfvars -- the root module itself is
# environment-agnostic (IaC-3: the same module tree deploys every environment,
# only inputs differ).

variable "environment" {
  description = "Deployment environment name: dev | staging | production."
  type        = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be one of: dev, staging, production."
  }
}

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-south-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VoiceOS VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones used for subnet placement (2 for HA, per V7 Ch2 multi-AZ topology)."
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b"]
}

variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
  default     = "voiceos"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.31"
}

variable "cpu_node_instance_type" {
  description = "Instance type for the CPU/media (burstable) node pool."
  type        = string
  default     = "m6i.xlarge"
}

variable "gpu_node_instance_type" {
  description = "Instance type for the tainted GPU node pool (K8S-2)."
  type        = string
  default     = "g5.2xlarge"
}

variable "data_node_instance_type" {
  description = "Instance type for the data (self-hosted MongoDB replica set) node pool."
  type        = string
  default     = "m6i.large"
}

variable "system_node_instance_type" {
  description = "Instance type for the system (platform/ops workloads) node pool."
  type        = string
  default     = "m6i.large"
}

variable "cpu_node_desired_count" {
  description = "Desired node count for the CPU/media node pool."
  type        = number
  default     = 2
}

variable "gpu_node_desired_count" {
  description = "Desired node count for the tainted GPU node pool."
  type        = number
  default     = 1
}

variable "data_node_desired_count" {
  description = "Desired node count for the data node pool (self-hosted MongoDB replica set size)."
  type        = number
  default     = 3
}

variable "system_node_desired_count" {
  description = "Desired node count for the system node pool."
  type        = number
  default     = 2
}

variable "postgres_instance_class" {
  description = "RDS Postgres instance class."
  type        = string
  default     = "db.r6g.large"
}

variable "postgres_multi_az" {
  description = "Whether RDS Postgres runs Multi-AZ (V7 Ch2: required for staging/production, optional for dev)."
  type        = bool
  default     = false
}

variable "postgres_allocated_storage_gb" {
  description = "RDS Postgres allocated storage, in GB."
  type        = number
  default     = 100
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type."
  type        = string
  default     = "cache.r6g.large"
}

variable "mongodb_ami_id" {
  description = "Ubuntu 22.04 AMI ID for the self-hosted MongoDB replica set nodes (region-specific; see modules/mongodb/variables.tf for the refresh command)."
  type        = string
}

variable "redis_multi_az" {
  description = "Whether ElastiCache Redis runs Multi-AZ with automatic failover."
  type        = bool
  default     = false
}

variable "service_names" {
  description = <<-EOT
    The full set of VoiceOS service names, one ECR repository (and one Helm
    sub-chart, see infra/helm/voiceos-platform/Chart.yaml) per service.

    Sprint-026.md's own Helm chart tree literally names 25 sub-charts (the
    prose elsewhere in the same file says "24 sub-charts" -- a stale count
    that doesn't match its own bulleted list). Resolved by implementing
    every literally-named chart rather than dropping one to force the
    number to match; see CHANGELOG.md's Sprint-026 entry.
  EOT
  type        = list(string)
  default = [
    "media-gateway", "audio-preprocessing", "vad-endpointing", "gpu-scheduler",
    "stt", "llm-runtime", "tts", "conversation-engine", "dialogue-manager",
    "policy-engine", "auth", "authz", "ai-governance", "tenant-management",
    "crm", "collections", "campaign-management", "contact-center", "billing",
    "metering", "analytics", "admin-portal", "ai-config", "integration-platform",
    "api-platform",
  ]
}

variable "tags" {
  description = "Common resource tags applied across every module."
  type        = map(string)
  default = {
    Project   = "voiceos-v2"
    ManagedBy = "terraform"
  }
}
