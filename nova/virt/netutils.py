# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# Copyright (c) 2010 Citrix Systems, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


"""Network-related utilities for supporting libvirt connection code."""


import netaddr

from nova.openstack.common import cfg

CONF = cfg.CONF
CONF.import_opt('use_ipv6', 'nova.netconf')
CONF.import_opt('injected_network_template', 'nova.virt.disk.api')

Template = None


def _late_load_cheetah():
    global Template
    if Template is None:
        t = __import__('Cheetah.Template', globals(), locals(),
                       ['Template'], -1)
        Template = t.Template


def get_net_and_mask(cidr):
    net = netaddr.IPNetwork(cidr)
    return str(net.ip), str(net.netmask)


def get_net_and_prefixlen(cidr):
    net = netaddr.IPNetwork(cidr)
    return str(net.ip), str(net._prefixlen)


def get_ip_version(cidr):
    net = netaddr.IPNetwork(cidr)
    return int(net.version)


def get_injected_network_template(network_info, use_ipv6=CONF.use_ipv6,
                                  template=CONF.injected_network_template):
    """
    return a rendered network template for the given network_info

    :param network_info:
       :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
    """

    nets = []
    ifc_num = -1
    have_injected_networks = False

    for vif in network_info:
        network = vif['network']
        ifc_num += 1

        if not network.get_meta('injected', False):
            continue
        if 'subnets' not in network:
            continue

        have_injected_networks = True
        v4_subnets = [subnet for subnet in network['subnets']
                      if subnet['version'] == 4]
        v4_subnets = [subnet for subnet in network['subnets']
                      if subnet['version'] == 6]

        address = gateway = netmask = broadcast = dns_servers = None
        address_v6 = gateway_v6 = netmask_v6 = None

        if len(v4_subnets) > 0:
            subnet = v4_subnets[0]
            address = subnet['ips'][0]['address']
            netmask = str(subnet.as_netaddr().netmask)
            gateway = subnet['gateway']['address']
            broadcast = str(subnet.as_netaddr().broadcast),
            dns_servers = [ip['address'] for ip in subnet['dns']]
        if use_ipv6:
            if len(v6_subnets) > 0:
                subnet = v6_subnets[0]
                address_v6 = subnet['ips'][0]['address']
                netmask_v6 = subnet.as_netaddr()._prefixlen
                gateway_v6 = subnet['gateway']['address']

        net_info = {'name': 'eth%d' % ifc_num,
               'address': address,
               'netmask': netmask,
               'gateway': gateway,
               'broadcast': broadcast,
               'dns': ' '.join(dns_servers),
               'address_v6': address_v6,
               'gateway_v6': gateway_v6,
               'netmask_v6': netmask_v6}
        nets.append(net_info)

    if have_injected_networks is False:
        return None

    if not template:
        return None

    _late_load_cheetah()

    ifc_template = open(template).read()
    return str(Template(ifc_template,
                        searchList=[{'interfaces': nets,
                                     'use_ipv6': use_ipv6}]))
