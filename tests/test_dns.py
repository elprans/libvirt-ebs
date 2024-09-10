from __future__ import annotations

from typing import (
    Generator,
    TYPE_CHECKING,
)

import time

import pytest

if TYPE_CHECKING:
    from mypy_boto3_route53 import Route53Client
    from mypy_boto3_route53.literals import (
        ChangeActionType,
        RRTypeType,
    )
    from mypy_boto3_route53.type_defs import (
        ChangeBatchTypeDef,
        ResourceRecordTypeDef,
        ResourceRecordSetOutputTypeDef,
    )


@pytest.fixture(scope="module")
def hosted_zone(
    route53: Route53Client,
) -> Generator[str, None, None]:
    response = route53.create_hosted_zone(
        Name="local-1.internal.", CallerReference=str(time.time())
    )
    zone_id = response["HostedZone"]["Id"].split("/")[-1]
    yield zone_id
    record_sets = route53.list_resource_record_sets(HostedZoneId=zone_id)
    change_batch: ChangeBatchTypeDef = {
        "Changes": [
            {
                "Action": "DELETE",
                "ResourceRecordSet": {
                    "Name": r["Name"],
                    "Type": r["Type"],
                    "TTL": r["TTL"],
                    "ResourceRecords": r["ResourceRecords"],
                },
            }
            for r in record_sets["ResourceRecordSets"]
            if (
                r["Type"] != "SOA"
                and (r["Type"] != "NS" or r["Name"] != "local-1.internal.")
            )
        ]
    }
    if change_batch["Changes"]:
        route53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch=change_batch,
        )
    route53.delete_hosted_zone(Id=zone_id)


def _change_record_set(
    client: Route53Client,
    zone_id: str,
    action: ChangeActionType,
    name: str,
    type: RRTypeType,
    values: list[str],
    ttl: int = 300,
) -> None:
    records: list[ResourceRecordTypeDef] = [{"Value": v} for v in values]
    change_batch: ChangeBatchTypeDef = {
        "Changes": [
            {
                "Action": action,
                "ResourceRecordSet": {
                    "Name": name,
                    "Type": type,
                    "TTL": ttl,
                    "ResourceRecords": records,
                },
            }
        ]
    }
    client.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch=change_batch,
    )


def _create_record_set(
    client: Route53Client,
    zone_id: str,
    name: str,
    type: RRTypeType,
    values: list[str],
    ttl: int = 300,
) -> None:
    _change_record_set(client, zone_id, "CREATE", name, type, values, ttl)


def _upsert_record_set(
    client: Route53Client,
    zone_id: str,
    name: str,
    type: RRTypeType,
    values: list[str],
    ttl: int = 300,
) -> None:
    _change_record_set(client, zone_id, "UPSERT", name, type, values, ttl)


def _delete_record_set(
    client: Route53Client,
    zone_id: str,
    name: str,
    type: RRTypeType,
    values: list[str],
    ttl: int = 300,
) -> None:
    _change_record_set(client, zone_id, "DELETE", name, type, values, ttl)


def _get_record_set(
    client: Route53Client,
    zone_id: str,
    name: str,
    type: RRTypeType,
) -> ResourceRecordSetOutputTypeDef | None:
    response = client.list_resource_record_sets(
        HostedZoneId=zone_id,
        StartRecordName=name,
        StartRecordType=type,
        MaxItems="1",
    )
    for record in response["ResourceRecordSets"]:
        if record["Name"] == name and record["Type"] == type:
            return record
    return None


def _assert_records(
    r1: list[ResourceRecordTypeDef],
    r2: list[ResourceRecordTypeDef],
) -> None:
    r1_sorted = list(sorted(r1, key=lambda r: r["Value"]))
    r2_sorted = list(sorted(r2, key=lambda r: r["Value"]))
    assert r1_sorted == r2_sorted


def _assert_record_set(
    record: ResourceRecordSetOutputTypeDef | None,
    values: list[str],
) -> None:
    assert record is not None
    _assert_records(record["ResourceRecords"], [{"Value": v} for v in values])


def _create_record_set_and_assert(
    client: Route53Client,
    zone_id: str,
    name: str,
    record_type: RRTypeType,
    values: list[str],
) -> None:
    _create_record_set(client, zone_id, name, record_type, values)
    record = _get_record_set(client, zone_id, name, record_type)
    _assert_record_set(record, values)


def _upsert_record_set_and_assert(
    client: Route53Client,
    zone_id: str,
    name: str,
    record_type: RRTypeType,
    values: list[str],
) -> None:
    _upsert_record_set(client, zone_id, name, record_type, values)
    record = _get_record_set(client, zone_id, name, record_type)
    _assert_record_set(record, values)


def _delete_record_set_and_assert(
    client: Route53Client,
    zone_id: str,
    name: str,
    record_type: RRTypeType,
    values: list[str],
) -> None:
    _delete_record_set(client, zone_id, name, record_type, values)
    record = _get_record_set(client, zone_id, name, record_type)
    assert record is None


def test_route53_A_record(
    route53: Route53Client,
    hosted_zone: str,
) -> None:
    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "A",
        ["192.0.2.1"],
    )

    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "A",
        ["192.0.2.1", "192.0.2.2"],
    )

    # Overlapping records.
    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test2.local-1.internal.",
        "A",
        ["192.0.2.2", "192.0.2.3"],
    )

    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "A",
        ["192.0.2.1"],
    )


def test_route53_AAAA_record(
    route53: Route53Client,
    hosted_zone: str,
) -> None:
    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "AAAA",
        ["e80::210:5aff:feaa:20a2"],
    )

    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "AAAA",
        ["e80::210:5aff:feaa:20a2", "e80::210:5aff:feaa:20b2"],
    )

    # Overlapping records.
    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test2.local-1.internal.",
        "AAAA",
        ["e80::210:5aff:feaa:20b2", "e80::210:5aff:feaa:20c2"],
    )

    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "AAAA",
        ["e80::210:5aff:feaa:20a2"],
    )


def test_route53_CNAME_record(
    route53: Route53Client,
    hosted_zone: str,
) -> None:
    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "foo.local-1.internal.",
        "A",
        ["192.0.2.1"],
    )

    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "bar.local-1.internal.",
        "A",
        ["192.0.2.2"],
    )

    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "spam.local-1.internal.",
        "A",
        ["192.0.2.3"],
    )

    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "CNAME",
        ["foo.local-1.internal."],
    )

    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "CNAME",
        ["foo.local-1.internal.", "bar.local-1.internal."],
    )

    # Overlapping records.
    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test2.local-1.internal.",
        "CNAME",
        ["bar.local-1.internal.", "spam.local-1.internal."],
    )

    _upsert_record_set_and_assert(
        route53,
        hosted_zone,
        "test1.local-1.internal.",
        "CNAME",
        ["foo.local-1.internal."],
    )


def test_route53_TXT_record(
    route53: Route53Client,
    hosted_zone: str,
) -> None:
    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "test.local-1.internal.",
        "TXT",
        ['"Text1"'],
    )


def test_route53_SRV_record(
    route53: Route53Client,
    hosted_zone: str,
) -> None:
    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "_sip._tcp.local-1.internal.",
        "SRV",
        ["10 5 5060 sipserver.local-1.internal."],
    )


def test_route53_NS_record(
    route53: Route53Client,
    hosted_zone: str,
) -> None:
    _create_record_set_and_assert(
        route53,
        hosted_zone,
        "sub.local-1.internal.",
        "NS",
        ["ns-123.awsdns-01.com.", "ns-456.awsdns-02.net."],
    )
