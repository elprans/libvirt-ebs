from __future__ import annotations

from typing import (
    TYPE_CHECKING,
)

import subprocess

import boto3
import pytest

if TYPE_CHECKING:
    import mypy_boto3_route53


@pytest.fixture(scope="module")
def route53(
    server: subprocess.Popen[bytes],
) -> mypy_boto3_route53.Route53Client:
    session = boto3.session.Session(
        aws_access_key_id="foo",
        aws_secret_access_key="bar",
        region_name="us-east-2",
    )
    return session.client("route53", endpoint_url="http://127.0.0.1:6666")
