# object-storage module -- S3 buckets: audio recordings, exports, backups
# (Sprint-026.md, V7 Ch2)
#
# Every bucket: private ACL, public access fully blocked, versioning
# enabled, SSE-KMS at rest, TLS-only bucket policy -- the AWS-native
# equivalent of the CPU node's LocalDiskObjectStore (Sprint-019: "no S3/GCS
# account exists for this project" was true before this sprint's IaC; this
# module is the real target-state replacement once an AWS account backs it).

locals {
  buckets = {
    recordings = "${var.name_prefix}-audio-recordings"
    exports    = "${var.name_prefix}-exports"
    backups    = "${var.name_prefix}-backups"
  }
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket = each.value
  tags   = merge(var.tags, { Name = each.value })
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      each.value.arn,
      "${each.value.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  policy = data.aws_iam_policy_document.tls_only[each.key].json
}

# Backups bucket: independent lifecycle -- transition to cheaper storage
# after 90 days, matching the collections/analytics 90-day retention
# convention already used for MongoDB TTL indexes (CPU_NODE_STATE.md §7.3).
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.this["backups"].id

  rule {
    id     = "transition-to-glacier"
    status = "Enabled"

    filter {}

    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}
