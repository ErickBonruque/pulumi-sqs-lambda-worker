"""Reusable Pulumi component: SQS queue + DLQ + container Lambda consumer."""

from sqs_lambda_worker.component import SqsLambdaWorker, WorkerArgs

__all__ = ["SqsLambdaWorker", "WorkerArgs"]
