from typing import (
    Generator,
)

import libvirt

import pytest


@pytest.fixture(scope="module")
def libvirt_net() -> Generator[libvirt.virNetwork, None, None]:
    conn = libvirt.open("qemu:///system")

    if not conn:
        raise Exception("Failed to open a connection to the libvirtd daemon")

    try:
        net_name = "libvirt_aws_test_network"
        net = conn.networkCreateXML(
            f"""
            <network>
                <name>{net_name}</name>
                <forward mode='nat' />
                <ip address='10.11.12.1' netmask='255.255.255.0' />
                <domain name='internal' localOnly='yes'/>
                <dns enable='yes'/>
            </network>
            """,
        )

        if net is None:
            raise Exception(
                f"Failed to create network: {conn.lastErrorName()}"
            )

        yield net

    finally:
        net.destroy()
        conn.close()
