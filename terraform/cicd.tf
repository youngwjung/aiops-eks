# 코드 파이프라인에서 사용할 버킷
resource "aws_s3_bucket" "codepipeline" {
  bucket = "${local.account_id}-codepipeline-storage"

  force_destroy = true
}

# 코드 파이프라인에서 사용할 IAM 역할
resource "aws_iam_policy" "codepipeline" {
  name = "codepipeline-policy"

  policy = <<-POLICY
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Action": [
            "s3:*",
            "codestar-connections:UseConnection",
            "codeconnections:UseConnection",
            "codebuild:*",
            "codecommit:*"
          ],
          "Effect": "Allow",
          "Resource": "*"
        }
      ]
    }
  POLICY
}

resource "aws_iam_role" "codepipeline" {
  name = "codepipeline-service-role"
  path = "/service-role/"

  assume_role_policy = <<-POLICY
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Action": "sts:AssumeRole",
          "Effect": "Allow",
          "Principal": {
            "Service": "codepipeline.amazonaws.com"
          }
        }
      ]
    }
  POLICY
}

resource "aws_iam_role_policy_attachment" "codepipeline" {
  role       = aws_iam_role.codepipeline.name
  policy_arn = aws_iam_policy.codepipeline.arn
}

# 코드 빌드에서 사용할 IAM 역할
resource "aws_iam_policy" "codebuild" {
  name = "codebuild-policy"

  policy = <<-POLICY
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "CloudWatchLogsPolicy",
          "Effect": "Allow",
          "Action": [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents"
          ],
          "Resource": "*"
        },
        {
          "Sid": "CodeConnectionsPolicy",
          "Effect": "Allow",
          "Action": [
            "codestar-connections:UseConnection",
            "codeconnections:UseConnection"
          ],
          "Resource": "*"
        },
        {
          "Sid": "S3GetObjectPolicy",
          "Effect": "Allow",
          "Action": [
            "s3:GetObject",
            "s3:GetObjectVersion"
          ],
          "Resource": "*"
        },
        {
          "Sid": "S3PutObjectPolicy",
          "Effect": "Allow",
          "Action": [
            "s3:PutObject"
          ],
          "Resource": "*"
        },
        {
          "Sid": "ECRPolicy",
          "Effect": "Allow",
          "Action": [
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchGetImage",
            "ecr:BatchCheckLayerAvailability",
            "ecr:CompleteLayerUpload",
            "ecr:GetAuthorizationToken",
            "ecr:InitiateLayerUpload",
            "ecr:PutImage",
            "ecr:DescribeImages",
            "ecr:UploadLayerPart"
          ],
          "Resource": "*"
        },
        {
          "Sid": "S3BucketIdentity",
          "Effect": "Allow",
          "Action": [
            "s3:GetBucketAcl",
            "s3:GetBucketLocation"
          ],
          "Resource": "*"
        },
        {
          "Sid": "CodePipelinePolicy",
          "Effect": "Allow",
          "Action": [
            "codepipeline:ListPipelineExecutions"
          ],
          "Resource": "*"
        },
        {
          "Sid": "CodeCommitPolicy",
          "Effect": "Allow",
          "Action": [
            "codecommit:*"
          ],
          "Resource": "*"
        }
      ]
    }
  POLICY
}

resource "aws_iam_role" "codebuild" {
  name = "codebuild-service-role"
  path = "/service-role/"

  assume_role_policy = <<-POLICY
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Principal": {
            "Service": "codebuild.amazonaws.com"
          },
          "Action": "sts:AssumeRole"
        }
      ]
    }
  POLICY
}

resource "aws_iam_role_policy_attachment" "codebuild" {
  role       = aws_iam_role.codebuild.name
  policy_arn = aws_iam_policy.codebuild.arn
}

# Argo CD를 설치할 네임스페이스
resource "kubernetes_namespace_v1" "argocd" {
  metadata {
    name = "argocd"
  }
}

# Argo CD 어드민 비밀번호의 bcrypt hash 생성
resource "htpasswd_password" "argocd" {
  password = "admin"
}

# Argo CD
resource "helm_release" "argocd" {
  name       = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.argocd_chart_version
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name

  values = [
    templatefile("${path.module}/helm-values/argocd.yaml", {
      hostname              = "argocd.${aws_route53_zone.this.name}"
      server_admin_password = htpasswd_password.argocd.bcrypt
      gateway_name          = local.envoy_gateway_name
      gateway_namespace     = local.envoy_gateway_namespace
      gateway_listener      = local.envoy_gateway_listener
    })
  ]
}

# CodeCommit 자격증명
resource "aws_iam_user" "argocd" {
  name = "argocd"
}

resource "aws_iam_user_policy_attachment" "argocd_codecommit" {
  user       = aws_iam_user.argocd.name
  policy_arn = "arn:aws:iam::aws:policy/AWSCodeCommitPowerUser"
}

resource "aws_iam_service_specific_credential" "argocd_codecommit" {
  service_name = "codecommit.amazonaws.com"
  user_name    = aws_iam_user.argocd.name
}

# CodeCommit 리포지토리 인증 정보
resource "kubernetes_secret_v1" "codecommit_cred" {
  metadata {
    name      = "codecommit"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "argocd.argoproj.io/secret-type" = "repo-creds"
    }
  }

  data = {
    type     = "git"
    url      = "https://git-codecommit.${local.aws_region}.amazonaws.com/v1/repos"
    username = aws_iam_service_specific_credential.argocd_codecommit.service_user_name
    password = aws_iam_service_specific_credential.argocd_codecommit.service_password
  }

  depends_on = [
    helm_release.argocd
  ]
}
