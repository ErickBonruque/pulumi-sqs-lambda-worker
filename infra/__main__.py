"""Pulumi program: the image thumbnail worker on AWS.

Resources: an S3 bucket for the outputs, an ECR repository, the container
image (built and pushed by Pulumi) and one ``SqsLambdaWorker``.
"""

from __future__ import annotations

from pathlib import Path

import pulumi
import pulumi_aws as aws
import pulumi_docker_build as docker_build

from sqs_lambda_worker import SqsLambdaWorker, WorkerArgs
from sqs_lambda_worker.policies import s3_prefix_read_write, to_json
from sqs_lambda_worker.tags import governance_tags

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = {
    "x86_64": docker_build.Platform.LINUX_AMD64,
    "arm64": docker_build.Platform.LINUX_ARM64,
}

config = pulumi.Config()
output_prefix = config.get("outputPrefix") or "images/"
architecture = config.get("architecture") or "x86_64"
if architecture not in PLATFORMS:
    raise ValueError(f"architecture must be one of {sorted(PLATFORMS)}")
tags = governance_tags()

# --- Output bucket: private, owner-enforced. Or reuse one with `bucketName`. ---
existing_bucket = config.get("bucketName")
if existing_bucket:
    bucket = aws.s3.Bucket.get("outputs", existing_bucket)
else:
    bucket = aws.s3.Bucket(
        "outputs",
        force_destroy=config.get_bool("forceDestroy") or False,
        tags=tags,
    )
    aws.s3.BucketPublicAccessBlock(
        "outputs-private",
        bucket=bucket.id,
        block_public_acls=True,
        block_public_policy=True,
        ignore_public_acls=True,
        restrict_public_buckets=True,
    )
    aws.s3.BucketOwnershipControls(
        "outputs-owner",
        bucket=bucket.id,
        rule=aws.s3.BucketOwnershipControlsRuleArgs(object_ownership="BucketOwnerEnforced"),
    )

# --- Container image: built from ./Dockerfile and pushed to a private ECR repo. ---
repository = aws.ecr.Repository(
    "worker-image",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True
    ),
    force_delete=True,
    tags=tags,
)
aws.ecr.LifecyclePolicy(
    "worker-image-keep-10",
    repository=repository.name,
    policy=to_json(
        {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Keep the last 10 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 10,
                    },
                    "action": {"type": "expire"},
                }
            ]
        }
    ),
)
token = aws.ecr.get_authorization_token_output(registry_id=repository.registry_id)
image = docker_build.Image(
    "worker-image-build",
    context=docker_build.BuildContextArgs(location=str(ROOT)),
    dockerfile=docker_build.DockerfileArgs(location=str(ROOT / "Dockerfile")),
    platforms=[PLATFORMS[architecture]],
    # `push=True` would add a default registry export; this one is explicit so the
    # pushed manifest is a plain Docker v2 manifest (Lambda rejects image indexes).
    push=False,
    exports=[
        docker_build.ExportArgs(
            registry=docker_build.ExportRegistryArgs(oci_media_types=False),
        )
    ],
    tags=[repository.repository_url.apply(lambda url: f"{url}:latest")],
    registries=[
        docker_build.RegistryArgs(
            address=repository.repository_url,
            username=token.user_name,
            password=token.password,
        )
    ],
)

# --- The worker itself. ---
worker = SqsLambdaWorker(
    "thumbnails",
    WorkerArgs(
        # `ref` includes the digest, so a new build always updates the function.
        image_uri=image.ref,
        architecture="arm64" if architecture == "arm64" else "x86_64",
        timeout_seconds=config.get_int("timeoutSeconds") or 30,
        memory_mb=config.get_int("memoryMb") or 1024,
        batch_size=config.get_int("batchSize") or 5,
        max_concurrency=config.get_int("maxConcurrency") or 5,
        environment={
            "OUTPUT_BUCKET": bucket.bucket,
            "OUTPUT_PREFIX": output_prefix,
            "ALLOWED_HOSTS": config.get("allowedHosts") or "upload.wikimedia.org",
            "THUMBNAIL_WIDTHS": config.get("thumbnailWidths") or "320,640,1280",
        },
        extra_policies={
            "outputs": bucket.arn.apply(
                lambda arn: to_json(s3_prefix_read_write(arn, output_prefix))
            )
        },
        alarm_actions=[topic] if (topic := config.get("alarmTopicArn")) else [],
    ),
)

pulumi.export("queue_url", worker.queue.url)
pulumi.export("dead_letter_queue_url", worker.dead_letter_queue.url)
pulumi.export("function_name", worker.function.name)
pulumi.export("log_group", worker.log_group.name)
pulumi.export("bucket", bucket.bucket)
pulumi.export("image_ref", image.ref)
