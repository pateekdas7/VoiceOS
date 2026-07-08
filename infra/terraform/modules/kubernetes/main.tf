# kubernetes module -- EKS cluster + 4 tiered node pools (Sprint-026.md, V7 Ch5)
#
# Node pools: cpu (burstable media/runtime services), gpu (tainted,
# GPU Scheduler-only, K8S-2), data (self-hosted MongoDB replica set), system
# (platform/ops workloads). Guaranteed QoS for hot-path pods (K8S-1) is
# enforced at the Helm chart level (requests == limits on every hot-path
# Deployment, see infra/helm/voiceos-platform/templates/_helpers.tpl) --
# this module only provisions the node capacity those pods schedule onto.

data "aws_iam_policy_document" "eks_cluster_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name_prefix}-eks-cluster"
  assume_role_policy = data.aws_iam_policy_document.eks_cluster_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

data "aws_iam_policy_document" "eks_node_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name_prefix}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.eks_node_assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "node_worker_policy" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni_policy" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_registry_policy" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_security_group" "node" {
  name_prefix = "${var.name_prefix}-eks-node-"
  description = "EKS node security group -- ingress limited to intra-cluster traffic only (deny-all default baseline)."
  vpc_id      = var.vpc_id

  ingress {
    description = "Node-to-node and control-plane-to-node traffic within the cluster."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  # EKS nodes genuinely need internet egress: pulling container images from
  # ECR (module.registry) and the EKS/STS control-plane APIs both traverse
  # the NAT gateway to a public AWS endpoint, and node bootstrapping pulls
  # OS packages. Narrower per-destination rules would need to enumerate
  # every AWS service endpoint CIDR by region and are better solved with
  # VPC endpoints (a future infra-hardening sprint), not a false-precision
  # allow-list here.
  #tfsec:ignore:aws-ec2-no-public-egress-sgr
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-eks-node-sg" })
}

resource "aws_eks_cluster" "this" {
  name     = var.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    endpoint_private_access = true
    # Private-only API server: kubectl/CI-CD access goes through the VPC
    # (VPN/bastion/CI runner inside the network), never the public internet.
    # A real `tfsec` run flagged the previous `endpoint_public_access = true`
    # (implicit 0.0.0.0/0 CIDR) as CRITICAL twice -- see CHANGELOG.md's
    # Sprint-026 entry.
    endpoint_public_access = false
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = var.cluster_kms_key_arn
    }
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.cluster_policy]
}

# ---------------------------------------------------------------------------
# CPU / media node pool -- burstable, untainted (default scheduling target)
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "cpu" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name_prefix}-cpu"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.cpu_node_instance_type]

  scaling_config {
    desired_size = var.cpu_node_desired_count
    min_size     = 1
    max_size     = var.cpu_node_desired_count * 3
  }

  labels = {
    "voiceos.io/node-pool" = "cpu"
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker_policy,
    aws_iam_role_policy_attachment.node_cni_policy,
    aws_iam_role_policy_attachment.node_registry_policy,
  ]
}

# ---------------------------------------------------------------------------
# GPU node pool -- tainted, only GPU Scheduler pods tolerate it (K8S-2)
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "gpu" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name_prefix}-gpu"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.gpu_node_instance_type]
  ami_type        = "AL2_x86_64_GPU"

  scaling_config {
    desired_size = var.gpu_node_desired_count
    min_size     = 0
    # EKS rejects max_size < 1 even when desired_size is 0 (dev scales the
    # GPU pool to zero to avoid idle GPU cost -- found running a real
    # `terraform plan`, not assumed).
    max_size = max(var.gpu_node_desired_count * 2, 1)
  }

  labels = {
    "voiceos.io/node-pool" = "gpu"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker_policy,
    aws_iam_role_policy_attachment.node_cni_policy,
    aws_iam_role_policy_attachment.node_registry_policy,
  ]
}

# ---------------------------------------------------------------------------
# Data node pool -- self-hosted MongoDB replica set members (V7 Ch2)
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "data" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name_prefix}-data"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.data_node_instance_type]

  scaling_config {
    desired_size = var.data_node_desired_count
    min_size     = var.data_node_desired_count
    max_size     = var.data_node_desired_count
  }

  labels = {
    "voiceos.io/node-pool" = "data"
  }

  taint {
    key    = "voiceos.io/data"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker_policy,
    aws_iam_role_policy_attachment.node_cni_policy,
    aws_iam_role_policy_attachment.node_registry_policy,
  ]
}

# ---------------------------------------------------------------------------
# System node pool -- platform/ops workloads (admin/billing/saas-ops/etc.)
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name_prefix}-system"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.system_node_instance_type]

  scaling_config {
    desired_size = var.system_node_desired_count
    min_size     = 1
    max_size     = var.system_node_desired_count * 3
  }

  labels = {
    "voiceos.io/node-pool" = "system"
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker_policy,
    aws_iam_role_policy_attachment.node_cni_policy,
    aws_iam_role_policy_attachment.node_registry_policy,
  ]
}
