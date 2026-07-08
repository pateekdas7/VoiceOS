# network module -- VPC, public/private subnets, NAT gateway, security groups
# (Sprint-026.md, V7 Ch2 Production Topology)
#
# One public subnet + one private subnet per AZ. EKS nodes, RDS, ElastiCache,
# and the self-hosted MongoDB replica set all live in the private subnets;
# only the NAT gateway's EIP and any public load balancers sit in public
# subnets.

locals {
  az_count = length(var.availability_zones)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count = local.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = var.availability_zones[count.index]
  # Deliberately false: nothing in this subnet needs an auto-assigned public
  # IP -- the NAT gateway's EIP is attached explicitly (aws_eip.nat below,
  # not via subnet auto-assign), and any future public ELB gets its own
  # public IP from the ELB service itself. A real `tfsec` run flagged
  # `true` here as HIGH; see CHANGELOG.md's Sprint-026 entry.
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name                     = "${var.name_prefix}-public-${var.availability_zones[count.index]}"
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_subnet" "private" {
  count = local.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + local.az_count)
  availability_zone = var.availability_zones[count.index]

  tags = merge(var.tags, {
    Name                              = "${var.name_prefix}-private-${var.availability_zones[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(var.tags, { Name = "${var.name_prefix}-nat-eip" })
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(var.tags, { Name = "${var.name_prefix}-nat" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-public-rt" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-private-rt" })
}

resource "aws_route_table_association" "public" {
  count = local.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count = local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Deny-all-by-default posture (Volume 4 security architecture) is achieved
# structurally, not via a placeholder security group: every consuming
# module (database/redis/mongodb/kubernetes) defines its own security group
# with zero ingress rules until it explicitly adds one -- AWS security
# groups are deny-by-default for anything not explicitly allowed, so there
# is nothing for a shared "deny-all" group to add. An earlier version of
# this module defined exactly such a placeholder (unattached to any
# resource, broad 0.0.0.0/0 egress) -- removed after a real `tfsec` run
# flagged both problems at once (an unattached SG achieves nothing, and its
# egress rule was itself a CRITICAL finding); see CHANGELOG.md's Sprint-026
# entry.
