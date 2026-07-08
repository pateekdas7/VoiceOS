# mongodb module -- self-hosted MongoDB replica set (Sprint-026.md: "MongoDB
# Atlas or self-hosted replica set")
#
# Self-hosted chosen: no MongoDB Atlas account exists for this project, same
# established precedent as the CPU node's self-hosted Vault (no cloud
# Vault/KMS account) and local-disk object storage (no S3/GCS account) before
# this sprint introduced real S3 buckets. Runs on the EKS "data" node pool's
# matching EC2 instance profile/AMI family (Ubuntu 22.04, matching
# CPU_NODE_STATE.md/GPU_NODE_STATE.md's actual OS) as standalone EC2
# instances rather than in-cluster pods -- a replica set's per-member stable
# network identity and dedicated EBS volumes are simpler to reason about
# outside StatefulSet+PVC mechanics for a first IaC pass; revisit as a
# StatefulSet in a future infra sprint if warranted.

resource "aws_security_group" "mongodb" {
  name_prefix = "${var.name_prefix}-mongodb-"
  description = "Self-hosted MongoDB replica set -- ingress limited to EKS nodes + intra-replica-set traffic only."
  vpc_id      = var.vpc_id

  dynamic "ingress" {
    for_each = var.allowed_security_group_ids
    content {
      description     = "MongoDB from EKS nodes"
      from_port       = 27017
      to_port         = 27017
      protocol        = "tcp"
      security_groups = [ingress.value]
    }
  }

  ingress {
    description = "Intra-replica-set traffic"
    from_port   = 27017
    to_port     = 27017
    protocol    = "tcp"
    self        = true
  }

  # These are real EC2 instances (self-hosted MongoDB, unlike the managed
  # RDS/ElastiCache above) -- they need internet egress for OS security
  # patches and the MongoDB package repository. Same accepted trade-off as
  # the EKS node security group; see that module's identical comment.
  #tfsec:ignore:aws-ec2-no-public-egress-sgr
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-mongodb-sg" })
}

resource "aws_instance" "replica" {
  count = var.replica_count

  ami                    = var.ami_id
  instance_type          = var.instance_type
  subnet_id              = element(var.private_subnet_ids, count.index % length(var.private_subnet_ids))
  vpc_security_group_ids = [aws_security_group.mongodb.id]

  root_block_device {
    volume_size = 100
    volume_type = "gp3"
    encrypted   = true
  }

  # IMDSv2-only: a real `tfsec` run flagged the default (IMDSv1 permitted)
  # as HIGH -- IMDSv1's non-session-oriented requests are a well-known SSRF
  # pivot to instance credentials.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  # MongoDB install + --auth + replica-set initiation is a Phase 2 deployment
  # concern, not baked into this Terraform resource -- same "IaC provisions
  # compute, a deployment script configures it" split as every other module
  # in this tree (deployment/gpu/bootstrap.sh, deployment/cpu/bootstrap.sh).
  # No such script exists yet for this module; tracked as follow-up work,
  # not claimed as already built.
  tags = merge(var.tags, { Name = "${var.name_prefix}-mongodb-${count.index}" })
}
