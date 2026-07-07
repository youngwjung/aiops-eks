# 소금가게 애플리케이션
locals {
  saltmart_apps = [
    "frontend",
    "order-service",
    "product-service",
    "review-service",
    "user-service"
  ]
}

# Git 리포지토리
resource "aws_codecommit_repository" "saltmart_apps" {
  for_each = toset(local.saltmart_apps)

  repository_name = each.key
}

# 소스코드 업로드
resource "null_resource" "git_config" {
  provisioner "local-exec" {
    command = <<EOT
      git config --global init.defaultBranch main
      git config --global user.email "dev@saltmart.com"
      git config --global user.name "Saltie"
    EOT
  }
}

resource "terraform_data" "saltmart_apps" {
  for_each = toset(local.saltmart_apps)

  provisioner "local-exec" {
    working_dir = "${path.module}/../code/${each.key}"
    command     = <<EOT
      git init
      git add .
      git commit -m "initial commit"
      git remote add origin codecommit::${local.aws_region}://${each.key}
      git push --set-upstream origin main
      rm -rf .git
    EOT
  }

  depends_on = [
    aws_codecommit_repository.saltmart_apps,
    null_resource.git_config
  ]
}

# ECR 리포지토리
module "saltmart_ecr" {
  source  = "terraform-aws-modules/ecr/aws"
  version = "3.2.0"

  for_each = toset(local.saltmart_apps)

  repository_name = each.key

  repository_image_tag_mutability = "IMMUTABLE_WITH_EXCLUSION"
  repository_image_tag_mutability_exclusion_filter = [
    {
      filter      = "latest*"
      filter_type = "WILDCARD"
    }
  ]

  create_lifecycle_policy  = false
  create_repository_policy = false
  attach_repository_policy = false

  repository_force_delete = true
}

# Helm 차트
resource "aws_codecommit_repository" "saltmart_helm_charts" {
  for_each = toset(local.saltmart_apps)

  repository_name = "${each.key}-helm-chart"
}

resource "terraform_data" "saltmart_helm_charts" {
  for_each = toset(local.saltmart_apps)

  provisioner "local-exec" {
    working_dir = "${path.module}/../code/helm-charts/${each.key}"
    command     = <<EOT
      git init
      git add .
      git commit -m "initial commit"
      git remote add origin codecommit::${local.aws_region}://${aws_codecommit_repository.saltmart_helm_charts[each.key].repository_name}
      git push --set-upstream origin main
      rm -rf .git
    EOT
  }

  depends_on = [
    aws_codecommit_repository.saltmart_helm_charts,
    null_resource.git_config
  ]
}

# 코드 빌드
resource "aws_cloudwatch_log_group" "codebuild" {
  for_each = toset(local.saltmart_apps)

  name              = "/aws/codebuild/${each.key}"
  retention_in_days = 7
}

resource "aws_codebuild_project" "saltmart_apps" {
  for_each = toset(local.saltmart_apps)

  name         = each.key
  service_role = aws_iam_role.codebuild.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:5.0"
    privileged_mode = "true"
    type            = "LINUX_CONTAINER"
  }

  logs_config {
    cloudwatch_logs {
      status     = "ENABLED"
      group_name = aws_cloudwatch_log_group.codebuild[each.key].name
    }
  }

  source {
    type      = "CODECOMMIT"
    location  = aws_codecommit_repository.saltmart_apps[each.key].clone_url_http
    buildspec = file("${path.module}/scripts/buildspec.yml")
  }

  depends_on = [
    aws_iam_role_policy_attachment.codebuild,
    terraform_data.saltmart_apps,
    terraform_data.saltmart_helm_charts
  ]
}

# 코드 파이프라인
resource "aws_codepipeline" "saltmart_apps" {
  for_each = toset(local.saltmart_apps)

  name          = each.key
  pipeline_type = "V2"
  role_arn      = aws_iam_role.codepipeline.arn

  artifact_store {
    type     = "S3"
    location = aws_s3_bucket.codepipeline.bucket
  }

  stage {
    name = "Source"

    action {
      name             = "Source"
      category         = "Source"
      owner            = "AWS"
      provider         = "CodeCommit"
      version          = "1"
      namespace        = "SourceVariables"
      output_artifacts = ["SourceArtifact"]

      configuration = {
        RepositoryName       = each.key
        BranchName           = "main"
        PollForSourceChanges = false
        OutputArtifactFormat = "CODEBUILD_CLONE_REF"
      }
    }
  }

  stage {
    name = "Build"

    action {
      category = "Build"

      configuration = {
        ProjectName = aws_codebuild_project.saltmart_apps[each.key].name
        EnvironmentVariables = jsonencode([
          {
            name  = "COMMIT_ID"
            value = "#{SourceVariables.CommitId}"
            type  = "PLAINTEXT"
          },
          {
            name  = "COMMIT_MESSAGE"
            value = "#{SourceVariables.CommitMessage}"
            type  = "PLAINTEXT"
          },
          {
            name  = "SERVICE_NAME"
            value = each.key
            type  = "PLAINTEXT"
          },
          {
            name  = "ECR_REPO"
            value = module.saltmart_ecr[each.key].repository_url
            type  = "PLAINTEXT"
          },
          {
            name  = "HELM_CHART_REPO"
            value = "codecommit::${local.aws_region}://${aws_codecommit_repository.saltmart_helm_charts[each.key].repository_name}"
            type  = "PLAINTEXT"
          }
        ])
      }

      input_artifacts  = ["SourceArtifact"]
      name             = "Build"
      namespace        = "BuildVariables"
      output_artifacts = ["BuildArtifact"]
      owner            = "AWS"
      provider         = "CodeBuild"
      version          = "1"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.codepipeline
  ]
}

resource "aws_iam_role" "codepipeline_trigger" {
  name = "codepipeline-trigger"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "events.amazonaws.com"
        }
      },
    ]
  })
}

resource "aws_iam_role_policy" "codepipeline_trigger" {
  name = "codepipeline-trigger"
  role = aws_iam_role.codepipeline_trigger.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "codepipeline:StartPipelineExecution",
        ]
        Effect   = "Allow"
        Resource = "*"
      },
    ]
  })
}

resource "aws_cloudwatch_event_rule" "saltmart_apps" {
  for_each = toset(local.saltmart_apps)

  name = "${each.key}-change"

  event_pattern = jsonencode({
    source      = ["aws.codecommit"]
    detail-type = ["CodeCommit Repository State Change"]
    resources = [
      aws_codecommit_repository.saltmart_apps[each.key].arn
    ]
    detail = {
      event = [
        "referenceCreated",
        "referenceUpdated"
      ]
      referenceType = ["branch"]
      referenceName = ["main"]
    }
  })
}

resource "aws_cloudwatch_event_target" "saltmart_apps" {
  for_each = toset(local.saltmart_apps)

  rule      = aws_cloudwatch_event_rule.saltmart_apps[each.key].name
  target_id = "codepipeline-AppPipeline"
  arn       = aws_codepipeline.saltmart_apps[each.key].arn
  role_arn  = aws_iam_role.codepipeline_trigger.arn
}

# ArgoCD에 Helm 차트 리포지토리 등록
resource "kubernetes_secret_v1" "saltmart_apps_argo_helm_repo" {
  for_each = toset(local.saltmart_apps)

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }

  data = {
    type = "git"
    url  = aws_codecommit_repository.saltmart_helm_charts[each.key].clone_url_http
  }

  depends_on = [
    helm_release.argocd
  ]
}

# Argo CD에 프로젝트 생성
resource "kubectl_manifest" "saltmart_argocd_project" {
  yaml_body = yamlencode({
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "AppProject"

    metadata = {
      name      = "saltmart"
      namespace = kubernetes_namespace_v1.argocd.metadata[0].name
      # 해당 프로젝트에 속한 애플리케이션이 존재할 경우 삭제 방지
      finalizers = [
        "resources-finalizer.argocd.argoproj.io"
      ]
    }

    spec = {
      sourceRepos = ["*"]
      destinations = [
        {
          name      = "*"
          server    = "*"
          namespace = "*"
        }
      ]
      clusterResourceWhitelist = [
        {
          group = "*"
          kind  = "*"
        }
      ]
    }
  })

  wait = true

  depends_on = [
    helm_release.argocd
  ]
}

# 소금가게 애플리케이션을 설치할 네임스페이스
resource "kubernetes_namespace_v1" "saltmart" {
  metadata {
    name = "saltmart"
  }
}

# 애플리케이션간 통신에 사용할 JWT 암호
resource "kubernetes_secret_v1" "saltmart_jwt_secret" {
  metadata {
    name      = "saltmart-jwt-secret"
    namespace = kubernetes_namespace_v1.saltmart.metadata[0].name
  }

  data = {
    jwt-secret = "qwerasdfzxcv"
  }
}

# Argo CD 애플리케이션 생성
resource "kubectl_manifest" "saltmart_argocd_app" {
  for_each = toset(local.saltmart_apps)

  yaml_body = yamlencode({
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"

    metadata = {
      name      = each.key
      namespace = kubernetes_namespace_v1.argocd.metadata[0].name
      finalizers = [
        "resources-finalizer.argocd.argoproj.io"
      ]
    }

    spec = {
      project = kubectl_manifest.saltmart_argocd_project.name

      source = {
        repoURL        = aws_codecommit_repository.saltmart_helm_charts[each.key].clone_url_http
        targetRevision = "HEAD"
        path           = "."
        helm = {
          releaseName = each.key
          valueFiles = [
            "values.yaml"
          ]
          valuesObject = {
            ingress = {
              hostname = "saltmart.${aws_route53_zone.this.name}"
            }

          }
        }
      }

      destination = {
        name      = "in-cluster"
        namespace = kubernetes_namespace_v1.saltmart.metadata[0].name
      }

      syncPolicy = {
        syncOptions = ["CreateNamespace=true"]
        automated   = {}
      }
    }
  })

  wait = true

  depends_on = [
    helm_release.argocd,
    kubernetes_secret_v1.saltmart_jwt_secret,
    kubectl_manifest.envoy_proxy
  ]
}

# AI 리뷰 서비스
resource "aws_codecommit_repository" "saltmart_review_summary" {
  repository_name = "review-summary-service"
}

resource "terraform_data" "saltmart_review_summary" {
  provisioner "local-exec" {
    working_dir = "${path.module}/../code/review-summary-service"
    command     = <<EOT
      git init
      git add .
      git commit -m "initial commit"
      git remote add origin codecommit::${local.aws_region}://review-summary-service
      git push --set-upstream origin main
      rm -rf .git
    EOT
  }

  depends_on = [
    aws_codecommit_repository.saltmart_review_summary,
    null_resource.git_config
  ]
}