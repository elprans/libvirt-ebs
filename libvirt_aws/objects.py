from __future__ import annotations
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Set,
    Tuple,
)

import collections
import functools
import ipaddress

import libvirt
import xmltodict


def get_volume(pool: libvirt.virStoragePool, name: str) -> Volume:
    for virvol in pool.listAllVolumes():
        vol = volume_from_xml(virvol.XMLDesc(0))
        if vol.name == name:
            return vol

    raise LookupError(f"volume {name} does not exist")


def get_all_volumes(
    pool: libvirt.virStoragePool,
) -> List[Volume]:
    vols = []

    for virvol in pool.listAllVolumes():
        vol = volume_from_xml(virvol.XMLDesc(0))
        vols.append(vol)

    return vols


def get_all_domains(
    conn: libvirt.virConnect,
) -> List[Domain]:
    domains = []

    for virdom in conn.listAllDomains():
        dom = domain_from_xml(virdom.XMLDesc(0))
        domains.append(dom)

    return domains


def get_vol_attachments(
    pool: libvirt.virStoragePool,
    volume: Volume,
) -> List[VolumeAttachment]:
    conn = pool.connect()
    attachments = []

    for dom in get_all_domains(conn):
        for disk in dom.disks:
            if volume.name == disk.volume and disk.pool == pool.name():
                attachments.append(disk.attachment)

    return attachments


@functools.lru_cache
def domain_from_xml(xml: str) -> Domain:
    return Domain(xmltodict.parse(xml)["domain"])


class Domain:
    def __init__(self, dom: Mapping[str, Any]) -> None:
        self._dom = dom
        self._disks: Optional[List[DiskDevice]] = None

    @property
    def name(self) -> str:
        return self._dom["name"]  # type: ignore[no-any-return]

    @property
    def disks(self) -> List[DiskDevice]:
        if self._disks is None:
            disks = self._dom["devices"]["disk"]
            if not isinstance(disks, list):
                disks = [disks]
            self._disks = [
                DiskDevice(self, d) for d in disks if d["@type"] == "volume"
            ]

        return self._disks


class DiskDevice:
    def __init__(self, dom: Domain, desc: Mapping[str, Any]) -> None:
        self._dom = dom
        self._desc = desc

    @property
    def volume(self) -> str:
        return self._desc["source"]["@volume"]  # type: ignore

    @property
    def pool(self) -> str:
        return self._desc["source"]["@pool"]  # type: ignore

    @property
    def attachment(self) -> VolumeAttachment:
        return VolumeAttachment(
            self._dom.name,
            self.volume,
            self.pool,
            self._desc["target"],
        )


@functools.lru_cache
def volume_from_xml(xml: str) -> Volume:
    return Volume(xml)


class Volume:
    def __init__(self, volxml: str) -> None:
        parsed = xmltodict.parse(volxml)
        self._vol = parsed["volume"]

    @property
    def name(self) -> str:
        return self._vol["name"]  # type: ignore[no-any-return]

    @property
    def key(self) -> str:
        return self._vol["key"]  # type: ignore[no-any-return]

    @property
    def target_path(self) -> str:
        return self._vol["target"]["path"]  # type: ignore[no-any-return]

    @property
    def backing_store(self) -> Optional[str]:
        bs = self._vol.get("backingStore")
        if bs is not None:
            return bs["path"]  # type: ignore[no-any-return]
        else:
            return None

    @property
    def capacity(self) -> int:
        return int(self._vol["capacity"]["#text"])


class VolumeAttachment:
    def __init__(
        self,
        domain: str,
        volume: str,
        pool: str,
        desc: Mapping[str, Any],
    ) -> None:
        self._domain = domain
        self._volume = volume
        self._pool = pool
        self._desc = desc

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def volume(self) -> str:
        return self._volume

    @property
    def pool(self) -> str:
        return self._pool

    @property
    def device(self) -> str:
        return self._desc["@dev"]  # type: ignore[no-any-return]


@functools.lru_cache
def network_from_xml(xml: str) -> Network:
    return Network(xml)


DNSRecords = MutableMapping[Tuple[str, str], Set[str]]


class Network:
    def __init__(self, netxml: str) -> None:
        parsed = xmltodict.parse(netxml)
        self._net = parsed["network"]
        self._records: Optional[DNSRecords] = None

    def dump_xml(self) -> str:
        return xmltodict.unparse(self._net)  # type: ignore [no-any-return]

    @property
    def name(self) -> str:
        name = self._net.get("name")
        assert isinstance(name, str)
        return name

    @property
    def uuid(self) -> str:
        uuid = self._net.get("uuid")
        assert isinstance(uuid, str)
        return uuid

    @property
    def domain(self) -> Optional[str]:
        dom = self._net.get("domain")
        if dom is not None:
            return dom["@name"]  # type: ignore [no-any-return]
        else:
            return None

    @property
    def dns_domain(self) -> Optional[str]:
        return fqdn(self.domain) if self.domain is not None else None

    def get_dns_domain_or_die(self) -> str:
        domain = self.dns_domain
        if not domain:
            raise ValueError("network does not define a DNS domain")
        return domain

    def get_dns_records(
        self,
        zone: str = "",
        exclude_zones: Optional[Set[str]] = None,
        include_soa_ns: bool = False,
        include_eager_cname: bool = False,
    ) -> DNSRecords:
        if self._records is None:
            self._records = self._extract_records()

        filters: List[Callable[[Tuple[str, str]], bool]] = []

        if zone:
            filters.append(lambda k: in_zone(k[1], zone))

        if exclude_zones:
            ez = exclude_zones
            filters.append(
                lambda k: not any(in_zone(k[1], zone) for zone in ez)
            )

        filtered_records: DNSRecords

        if filters:
            filtered_records = {
                k: v
                for k, v in self._records.items()
                if all(f(k) for f in filters)
            }
        else:
            filtered_records = self._records

        records: DNSRecords = {}
        for (rt, name), v in filtered_records.items():
            if rt == "TXT" and name.startswith("@@ns."):
                rt = "NS"
                name = name[5:]
                # Strip quotes
                vals: Set[str] = set()
                for r in v:
                    vals.update((item[1:-1] for item in r.split(",")))
                v = vals
            elif rt == "TXT" and name.startswith("@@cname."):
                rt = "CNAME"
                name = name[8:]
                # Strip quotes
                vals = set()
                for r in v:
                    vals.update((item[1:-1] for item in r.split(",")))
                v = vals
            records[(rt, name)] = v

        if not include_eager_cname:
            cnames = [name for rt, name in records if rt == "CNAME"]
            for cname in cnames:
                # If a record is a CNAME there would be a corresponding
                # A record for it, because we eagerly resolve it at
                # creation time.  Make sure such records are elided.
                records.pop(("A", cname), None)

        if include_soa_ns:
            domain = self.dns_domain
            if not domain:
                raise ValueError("network does not define a DNS domain")

            if not zone:
                zone = domain

            if zone:
                soa_ns = {
                    ("SOA", zone): {
                        f"gw.{domain} hostmaster.gw.{domain} "
                        + "1 1200 180 1209600 600",
                    },
                    ("NS", zone): {
                        f"gw.{domain}",
                    },
                }
                soa_ns.update(records)
                records = soa_ns

        return records

    @property
    def dns_records(self) -> DNSRecords:
        return self.get_dns_records()

    def _extract_records(self) -> DNSRecords:
        dns = self._net.get("dns")
        if not dns:
            return {}

        assert isinstance(dns, dict)

        memo: DNSRecords = collections.defaultdict(set)
        for k, v in dns.items():
            if isinstance(v, list):
                vv = v
            else:
                vv = [v]
            if k == "txt":
                for v in vv:
                    memo[k.upper(), v["@name"]].add(v["@value"])
            elif k == "srv":
                for v in vv:
                    name = f"_{v['@service']}._{v['@protocol']}"
                    domain = v.get("@domain")
                    if domain:
                        name += f".{domain}"
                    val = " ".join(
                        (
                            v.get("@priority", "0"),
                            v.get("@weight", "0"),
                            v.get("@port", "0"),
                            v.get("@target", "."),
                        )
                    )
                    memo[k.upper(), name].add(val)
            elif k == "host":
                for v in vv:
                    addr = ipaddress.ip_address(v["@ip"])
                    rectype = "A" if addr.version == 4 else "AAAA"
                    h = v["hostname"]
                    if isinstance(h, list):
                        hh = h
                    else:
                        hh = [h]
                    for h in hh:
                        memo[rectype, fqdn(h)].add(v["@ip"])

        return memo

    def set_dns_records(self, records: DNSRecords) -> None:
        self._records = records
        self._update_records(records)

    def _update_records(self, records: DNSRecords) -> None:
        try:
            dns = self._net["dns"]
        except KeyError:
            dns = self._net["dns"] = {}

        assert isinstance(dns, dict)
        # Remove all current records
        dns = {k: v for k, v in dns.items() if k not in {"txt", "srv", "host"}}

        hosts: Dict[str, List[str]] = collections.defaultdict(list)

        for (type, name), values in records.items():
            if type in {"A", "AAAA"}:
                for value in values:
                    hosts[value].append(name)
            elif type == "TXT":
                if "txt" not in dns:
                    dns["txt"] = []
                for value in values:
                    dns["txt"].append({"@name": name, "@value": value})
            elif type == "SRV":
                if "srv" not in dns:
                    dns["srv"] = []
                for value in values:
                    prio, weight, port, target = value.split(" ", 3)
                    dns["srv"].append(
                        {
                            "@name": name,
                            "@priority": prio,
                            "@weight": weight,
                            "@port": port,
                            "@target": target,
                        }
                    )

        if hosts:
            dns["host"] = [
                {"@ip": k, "hostname": v} for k, v in hosts.values()
            ]

    def _resolve_cname(self, target: str, records: DNSRecords) -> set[str]:
        fq_target = fqdn(target)
        addrs = records.get(("A", fq_target))
        if addrs:
            return addrs
        cnames = records.get(("CNAME", fq_target))
        if cnames:
            assert len(cnames) == 1, "only one value in CNAME record expected"
            return self._resolve_cname(next(iter(cnames)), records)

        return set()

    def get_dns_diff(
        self,
        records: DNSRecords,
    ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        current = self.get_dns_records()
        current_all = self.get_dns_records(include_eager_cname=True)

        host_map: Dict[str, Set[str]] = collections.defaultdict(set)
        for (type, name), values in current_all.items():
            if type in {"A", "AAAA"}:
                for addr in values:
                    host_map[addr].add(name)

        current_hosts = set(host_map)

        mod_hosts: Set[str] = set()

        added: List[Tuple[str, str]] = []
        deleted: List[Tuple[str, str]] = []

        norm_records = {
            (type, fqdn(name)): values
            for (type, name), values in records.items()
        }

        new_and_current = collections.ChainMap(norm_records, current_all)

        # Process all new or modified records
        for (type, name), values in norm_records.items():
            prev = current.get((type, name), set())
            if prev == values:
                # No changes
                continue

            if type in {"A", "AAAA"}:
                added_addrs = values - prev
                for added_addr in added_addrs:
                    host_map[added_addr].add(name)
                mod_hosts.update(added_addrs)

                deleted_addrs = prev - values
                for deleted_addr in deleted_addrs:
                    host_map[deleted_addr].discard(name)
                mod_hosts.update(deleted_addrs)

            elif type == "CNAME":
                # Unfortunately, libvirt does not support adding
                # CNAME records, so we have to do the next best thing:
                # try to resolve the target and add it as A instead.
                added_addrs = set()
                for added_target in values - prev:
                    added_addrs.update(
                        self._resolve_cname(added_target, new_and_current)
                    )
                for added_addr in added_addrs:
                    host_map[added_addr].add(name)
                mod_hosts.update(added_addrs)

                deleted_addrs = set()
                for deleted_target in prev - values:
                    deleted_addrs.update(
                        self._resolve_cname(deleted_target, current_all)
                    )
                for deleted_addr in deleted_addrs:
                    host_map[deleted_addr].discard(name)
                mod_hosts.update(deleted_addrs)

                if prev:
                    deleted.append(("txt", _dns_xml_cname(name, prev)))

                added.append(("txt", _dns_xml_cname(name, values)))

            elif type == "TXT":
                deleted.extend(
                    ("txt", _dns_xml_txt(name, v)) for v in prev - values
                )
                added.extend(
                    ("txt", _dns_xml_txt(name, v)) for v in values - prev
                )

            elif type == "NS":
                if prev:
                    deleted.append(("txt", _dns_xml_ns(name, prev)))
                added.append(("txt", _dns_xml_ns(name, values)))

            elif type == "SRV":
                deleted.extend(
                    ("srv", _dns_xml_srv(name, v)) for v in prev - values
                )
                added.extend(
                    ("srv", _dns_xml_srv(name, v)) for v in values - prev
                )
            else:
                raise ValueError(f"unsupported resource record type: {type}")

        # Process all deleted records
        for key in frozenset(current) - frozenset(norm_records):
            values = current[key]
            type, name = key

            if type == "SRV":
                deleted.extend(("srv", _dns_xml_srv(name, v)) for v in values)
            elif type == "TXT":
                deleted.extend(("txt", _dns_xml_txt(name, v)) for v in values)
            elif type == "NS":
                deleted.append(("txt", _dns_xml_ns(name, values)))
            elif type in {"A", "AAAA"}:
                mod_hosts.update(values)
                for addr in values:
                    host_map[addr].discard(name)
            elif type == "CNAME":
                # Unfortunately, libvirt does not support adding
                # CNAME records, so we have to do the next best thing:
                # try to resolve the target and add it as A instead.
                deleted_addrs = set()
                for target in values:
                    deleted_addrs.update(
                        self._resolve_cname(target, current_all)
                    )
                for deleted_addr in deleted_addrs:
                    host_map[deleted_addr].discard(name)
                mod_hosts.update(deleted_addrs)

                deleted.append(("txt", _dns_xml_cname(name, values)))
            else:
                raise ValueError(f"unsupported resource record type: {type}")

        # Apply changes to the host mappings
        deleted.extend(
            ("host", _dns_xml_host(addr, []))
            for addr in mod_hosts & current_hosts
        )

        for addr in mod_hosts:
            hosts = host_map.get(addr)
            if hosts:
                added.append(("host", _dns_xml_host(addr, hosts)))

        return added, deleted

    @property
    def ip_network(self) -> ipaddress.IPv4Network:
        ip = self._net.get("ip")
        if ip is None:
            raise ValueError("network does not define an IP block")
        if ip["@family"] != "ipv4":
            raise ValueError("network is not an IPv4 network")
        return ipaddress.IPv4Network(
            f"{ip['@address']}/{ip['@prefix']}",
            strict=False,
        )

    @property
    def static_ip_range(
        self,
    ) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
        ip = self._net.get("ip")
        if ip is None:
            raise ValueError("network does not define an IP block")
        if ip["@family"] != "ipv4":
            raise ValueError("network is not an IPv4 network")
        dhcp = ip.get("dhcp")
        if dhcp is None:
            raise ValueError("network does not define a DHCP block")
        dhcpRange = dhcp.get("range")
        if dhcpRange is None:
            raise ValueError("network does not define a DHCP range block")

        ip_iter = self.ip_network.hosts()
        next(ip_iter)
        start = next(ip_iter)

        return (
            start,
            ipaddress.IPv4Address(dhcpRange["@start"]),
        )


def fqdn(hostname: str) -> str:
    return f"{hostname}." if not hostname.endswith(".") else hostname


def in_zone(hostname: str, zone: str) -> bool:
    hostname = fqdn(hostname)
    return hostname == zone or hostname.endswith(f".{zone}")


def _dns_xml_host(addr: str, hosts: list[str] |  set[str]) -> str:
    if hosts:
        return xmltodict.unparse(  # type: ignore
            {
                "host": {
                    "@ip": addr,
                    "hostname": [{"#text": h} for h in hosts],
                }
            }
        )
    else:
        return xmltodict.unparse(  # type: ignore
            {
                "host": {
                    "@ip": addr,
                }
            }
        )


def _dns_xml_txt(name: str, value: str) -> str:
    return xmltodict.unparse(  # type: ignore
        {"txt": {"@name": name, "@value": value}},
    )


def _dns_xml_cname(name: str, values: list[str] | set[str]) -> str:
    return _dns_xml_txt(
        f"@@cname.{name}",
        ",".join(f'"{fqdn(value)}"' for value in sorted(values)),
    )


def _dns_xml_ns(name: str, values: list[str] | set[str]) -> str:
    return _dns_xml_txt(
        f"@@ns.{name}",
        ",".join(f'"{fqdn(value)}"' for value in sorted(values)),
    )


def _dns_xml_srv(name: str, value: str) -> str:
    parts = name.split(".", maxsplit=2)
    if len(parts) == 2:
        service, protocol = parts
        domain = None
    else:
        service, protocol, domain = parts
    priority, weight, port, target = value.split(" ", maxsplit=3)

    service = service.lstrip("_")
    protocol = protocol.lstrip("_")

    return xmltodict.unparse(  # type: ignore
        {
            "srv": {
                "@service": service,
                "@protocol": protocol,
                "@domain": domain,
                "@priority": priority,
                "@weight": weight,
                "@port": port,
                "@target": target,
            }
        }
    )
