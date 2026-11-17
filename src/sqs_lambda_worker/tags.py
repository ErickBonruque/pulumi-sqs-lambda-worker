"""Tags that every resource gets, so cost and ownership are easy to trace."""

from __future__ import annotations

from typing import Literal

import pulumi

DataClassification = Literal["public", "internal", "confidential"]


def governance_tags(
    classification: DataClassification = "internal",
    **extra: str,
) -> dict[str, str]:
    stack = pulumi.get_stack()
    return {
        "Project": pulumi.get_project(),
        "Stack": stack,
        "Environment": "prod" if stack == "prod" else "nonprod",
        "ManagedBy": "pulumi",
        "DataClassification": classification,
        **extra,
    }
