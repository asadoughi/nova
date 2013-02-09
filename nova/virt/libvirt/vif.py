# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (C) 2011 Midokura KK
# Copyright (C) 2011 Nicira, Inc
# Copyright 2011 OpenStack LLC.
# All Rights Reserved.
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

"""VIF drivers for libvirt."""

import copy

from nova import exception
from nova.network import linux_net
from nova.network import model as network_model
from nova.openstack.common import cfg
from nova.openstack.common import log as logging
from nova import utils

from nova.virt.libvirt import config as vconfig
from nova.virt.libvirt import designer

LOG = logging.getLogger(__name__)

libvirt_vif_opts = [
    # quantum_ovs_bridge is used, if Quantum provides Nova
    # the 'vif_type' portbinding field
    cfg.StrOpt('libvirt_ovs_bridge',
               default='br-int',
               help='Name of Integration Bridge used by Open vSwitch'),
    cfg.BoolOpt('libvirt_use_virtio_for_bridges',
                default=True,
                help='Use virtio for bridge interfaces with KVM/QEMU'),
]

CONF = cfg.CONF
CONF.register_opts(libvirt_vif_opts)
CONF.import_opt('libvirt_type', 'nova.virt.libvirt.driver')
CONF.import_opt('use_ipv6', 'nova.netconf')

# Since libvirt 0.9.11, <interface type='bridge'>
# supports OpenVSwitch natively.
LIBVIRT_OVS_VPORT_VERSION = 9011


def is_vif_model_valid_for_virt(virt_type, vif_model):
        valid_models = {
            'qemu': ['virtio', 'ne2k_pci', 'pcnet', 'rtl8139', 'e1000'],
            'kvm': ['virtio', 'ne2k_pci', 'pcnet', 'rtl8139', 'e1000'],
            'xen': ['netfront', 'ne2k_pci', 'pcnet', 'rtl8139', 'e1000'],
            'lxc': [],
            'uml': [],
            }

        if vif_model is None:
            return True

        if virt_type not in valid_models:
            raise exception.UnsupportedVirtType(virt=virt_type)

        return vif_model in valid_models[virt_type]


class LibvirtBaseVIFDriver(object):

    def __init__(self, get_connection):
        self.get_connection = get_connection
        self.libvirt_version = None

    def has_libvirt_version(self, want):
        if self.libvirt_version is None:
            conn = self.get_connection()
            self.libvirt_version = conn.getLibVersion()

        if self.libvirt_version >= want:
            return True
        return False

    def get_vif_devname(self, vif):
        if 'devname' in vif:
            return vif['devname']
        return ("nic" + vif['id'])[:network_model.NIC_NAME_LEN]

    def get_config(self, instance, vif, image_meta):
        conf = vconfig.LibvirtConfigGuestInterface()
        # Default to letting libvirt / the hypervisor choose the model
        model = None
        driver = None

        # If the user has specified a 'vif_model' against the
        # image then honour that model
        if image_meta:
            vif_model = image_meta.get('properties', {}).get('vif_model')
            if vif_model is not None:
                model = vif_model

        # Else if the virt type is KVM/QEMU, use virtio according
        # to the global config parameter
        if (model is None and
            CONF.libvirt_type in ('kvm', 'qemu') and
            CONF.libvirt_use_virtio_for_bridges):
            model = "virtio"

        # Workaround libvirt bug, where it mistakenly
        # enables vhost mode, even for non-KVM guests
        if model == "virtio" and CONF.libvirt_type == "qemu":
            driver = "qemu"

        if not is_vif_model_valid_for_virt(CONF.libvirt_type,
                                           model):
            raise exception.UnsupportedHardware(model=model,
                                                virt=CONF.libvirt_type)

        designer.set_vif_guest_frontend_config(
            conf, vif['address'], model, driver)

        return conf

    def plug(self, instance, vif):
        pass

    def unplug(self, instance, vif):
        pass


class LibvirtGenericVIFDriver(LibvirtBaseVIFDriver):
    """Generic VIF driver for libvirt networking."""

    def get_bridge_name(self, vif):
        return vif['network']['bridge']

    def get_ovs_interfaceid(self, vif):
        return vif.get('ovs_interfaceid')

    def get_br_name(self, iface_id):
        return ("qbr" + iface_id)[:network_model.NIC_NAME_LEN]

    def get_veth_pair_names(self, iface_id):
        return (("qvb%s" % iface_id)[:network_model.NIC_NAME_LEN],
                ("qvo%s" % iface_id)[:network_model.NIC_NAME_LEN])

    def get_firewall_required(self):
        # TODO(berrange): Extend this to use information from VIF model
        # which can indicate whether the network provider (eg Quantum)
        # has already applied firewall filtering itself.
        if CONF.firewall_driver != "nova.virt.firewall.NoopFirewallDriver":
            return True
        return False

    def get_config_bridge(self, instance, vif, image_meta):
        """Get VIF configurations for bridge type."""
        conf = super(LibvirtGenericVIFDriver,
                     self).get_config(instance,
                                      vif,
                                      image_meta)

        designer.set_vif_host_backend_bridge_config(
            conf, self.get_bridge_name(vif),
            self.get_vif_devname(vif))

        mac_id = vif['address'].replace(':', '')
        name = "nova-instance-" + instance['name'] + "-" + mac_id
        primary_addr = vif['network']['subnets'][0]['ips'][0]['address']
        dhcp_server = ra_server = ipv4_cidr = ipv6_cidr = None

        network = vif.get('network')
        if network and network.get('subnets'):
            dhcp_server = network['subnets'][0].get_meta('dhcp_server', None)
            if CONF.use_ipv6:
                v6_subnets = [s for s in network['subnets']
                              if s['version'] == 6]
                if len(v6_subnets) > 0 and v6_subnets[0].get('gateway'):
                    ra_server = v6_subnets[0]['gateway']['address'] + "/128"
            if CONF.allow_same_net_traffic:
                v4_subnets = [s for s in network['subnets']
                              if s['version'] == 4]
                v6_subnets = [s for s in network['subnets']
                              if s['version'] == 6]

                if len(v4_subnets) > 0:
                    ipv4_cidr = v4_subnets[0]['cidr']
                if CONF.use_ipv6:
                    if len(v6_subnets) > 0:
                        ipv6_cidr = v6_subnets[0]['cidr']

        if self.get_firewall_required():
            designer.set_vif_host_backend_filter_config(
                conf, name, primary_addr, dhcp_server,
                ra_server, CONF.allow_same_net_traffic,
                ipv4_cidr, ipv6_cidr)

        return conf

    def get_config_ovs_ethernet(self, instance, vif, image_meta):
        conf = super(LibvirtGenericVIFDriver,
                     self).get_config(instance,
                                      vif,
                                      image_meta)

        dev = self.get_vif_devname(vif)
        designer.set_vif_host_backend_ethernet_config(conf, dev)

        return conf

    def get_config_ovs_bridge(self, instance, vif, image_meta):
        conf = super(LibvirtGenericVIFDriver,
                     self).get_config(instance,
                                      vif,
                                      image_meta)

        designer.set_vif_host_backend_ovs_config(
            conf, self.get_bridge_name(vif),
            self.get_ovs_interfaceid(vif),
            self.get_vif_devname(vif))

        return conf

    def get_config_ovs_hybrid(self, instance, vif, image_meta):
        newvif = copy.deepcopy(vif)
        newvif['network']['bridge'] = self.get_br_name(vif['id'])
        return self.get_config_bridge(instance,
                                      newvif,
                                      image_meta)

    def get_config_ovs(self, instance, vif, image_meta):
        if self.get_firewall_required():
            return self.get_config_ovs_hybrid(instance, vif, image_meta)
        elif self.has_libvirt_version(LIBVIRT_OVS_VPORT_VERSION):
            return self.get_config_ovs_bridge(instance, vif, image_meta)
        else:
            return self.get_config_ovs_ethernet(instance, vif, image_meta)

    def get_config_802qbg(self, instance, vif, image_meta):
        conf = super(LibvirtGenericVIFDriver,
                     self).get_config(instance,
                                      vif,
                                      image_meta)

        params = vif['qbg_params']
        designer.set_vif_host_backend_802qbg_config(
            conf, vif['network'].get_meta('interface'),
            params['managerid'],
            params['typeid'],
            params['typeidversion'],
            params['instanceid'])

        return conf

    def get_config_802qbh(self, instance, vif, image_meta):
        conf = super(LibvirtGenericVIFDriver,
                     self).get_config(instance,
                                      vif,
                                      image_meta)

        params = vif['qbh_params']
        designer.set_vif_host_backend_802qbh_config(
            conf, vif['network'].get_meta('interface'),
            params['profileid'])

        return conf

    def get_config(self, instance, vif, image_meta):
        vif_type = vif['type']

        LOG.debug(_("vif_type=%(vif_type)s instance=%(instance)s "
                    "vif=%(vif)s")
                  % locals())

        if vif_type is None:
            raise exception.NovaException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))

        if vif_type == network_model.VIF_TYPE_BRIDGE:
            return self.get_config_bridge(instance, vif, image_meta)
        elif vif_type == network_model.VIF_TYPE_OVS:
            return self.get_config_ovs(instance, vif, image_meta)
        elif vif_type == network_model.VIF_TYPE_802_QBG:
            return self.get_config_802qbg(instance, vif, image_meta)
        elif vif_type == network_model.VIF_TYPE_802_QBH:
            return self.get_config_802qbh(instance, vif, image_meta)
        else:
            raise exception.NovaException(
                _("Unexpected vif_type=%s") % vif_type)

    def plug_bridge(self, instance, vif):
        """Ensure that the bridge exists, and add VIF to it."""
        super(LibvirtGenericVIFDriver,
              self).plug(instance, vif)

        network = vif['network']
        if (not network.get_meta('multi_host', False) and
            network.get_meta('should_create_bridge', False)):
            if network.get_meta('should_create_vlan', False):
                iface = CONF.vlan_interface or \
                    network.get_meta('bridge_interface')
                LOG.debug(_('Ensuring vlan %(vlan)s and bridge %(bridge)s'),
                          {'vlan': network['vlan'],
                           'bridge': self.get_bridge_name(vif)},
                          instance=instance)
                linux_net.LinuxBridgeInterfaceDriver.ensure_vlan_bridge(
                                             network.get_meta('vlan'),
                                             self.get_bridge_name(vif),
                                             iface)
            else:
                iface = CONF.flat_interface or \
                    network.get_meta('bridge_interface')
                LOG.debug(_("Ensuring bridge %s"),
                          self.get_bridge_name(vif), instance=instance)
                linux_net.LinuxBridgeInterfaceDriver.ensure_bridge(
                                        self.get_bridge_name(vif),
                                        iface)

    def plug_ovs_ethernet(self, instance, vif):
        super(LibvirtGenericVIFDriver,
              self).plug(instance, vif)

        iface_id = self.get_ovs_interfaceid(vif)
        dev = self.get_vif_devname(vif)
        linux_net.create_tap_dev(dev)
        linux_net.create_ovs_vif_port(self.get_bridge_name(vif),
                                      dev, iface_id, vif['address'],
                                      instance['uuid'])

    def plug_ovs_bridge(self, instance, vif):
        """No manual plugging required."""
        super(LibvirtGenericVIFDriver,
              self).plug(instance, vif)

    def plug_ovs_hybrid(self, instance, vif):
        """Plug using hybrid strategy

        Create a per-VIF linux bridge, then link that bridge to the OVS
        integration bridge via a veth device, setting up the other end
        of the veth device just like a normal OVS port.  Then boot the
        VIF on the linux bridge using standard libvirt mechanisms.
        """
        super(LibvirtGenericVIFDriver,
              self).plug(instance, vif)

        iface_id = self.get_ovs_interfaceid(vif)
        br_name = self.get_br_name(vif['id'])
        v1_name, v2_name = self.get_veth_pair_names(vif['id'])

        if not linux_net.device_exists(br_name):
            utils.execute('brctl', 'addbr', br_name, run_as_root=True)

        if not linux_net.device_exists(v2_name):
            linux_net._create_veth_pair(v1_name, v2_name)
            utils.execute('ip', 'link', 'set', br_name, 'up', run_as_root=True)
            utils.execute('brctl', 'addif', br_name, v1_name, run_as_root=True)
            linux_net.create_ovs_vif_port(self.get_bridge_name(vif),
                                          v2_name, iface_id, vif['address'],
                                          instance['uuid'])

    def plug_ovs(self, instance, vif):
        if self.get_firewall_required():
            self.plug_ovs_hybrid(instance, vif)
        elif self.has_libvirt_version(LIBVIRT_OVS_VPORT_VERSION):
            self.plug_ovs_bridge(instance, vif)
        else:
            self.plug_ovs_ethernet(instance, vif)

    def plug_802qbg(self, instance, vif):
        super(LibvirtGenericVIFDriver,
              self).plug(instance, vif)

    def plug_802qbh(self, instance, vif):
        super(LibvirtGenericVIFDriver,
              self).plug(instance, vif)

    def plug(self, instance, vif):
        vif_type = vif['type']

        LOG.debug(_("vif_type=%(vif_type)s instance=%(instance)s "
                    "vif=%(vif)s")
                  % locals())

        if vif_type is None:
            raise exception.NovaException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))

        if vif_type == network_model.VIF_TYPE_BRIDGE:
            self.plug_bridge(instance, vif)
        elif vif_type == network_model.VIF_TYPE_OVS:
            self.plug_ovs(instance, vif)
        elif vif_type == network_model.VIF_TYPE_802_QBG:
            self.plug_802qbg(instance, vif)
        elif vif_type == network_model.VIF_TYPE_802_QBH:
            self.plug_802qbh(instance, vif)
        else:
            raise exception.NovaException(
                _("Unexpected vif_type=%s") % vif_type)

    def unplug_bridge(self, instance, vif):
        """No manual unplugging required."""
        super(LibvirtGenericVIFDriver,
              self).unplug(instance, vif)

    def unplug_ovs_ethernet(self, instance, vif):
        """Unplug the VIF by deleting the port from the bridge."""
        super(LibvirtGenericVIFDriver,
              self).unplug(instance, vif)

        try:
            linux_net.delete_ovs_vif_port(self.get_bridge_name(vif),
                                          self.get_vif_devname(vif))
        except exception.ProcessExecutionError:
            LOG.exception(_("Failed while unplugging vif"), instance=instance)

    def unplug_ovs_bridge(self, instance, vif):
        """No manual unplugging required."""
        super(LibvirtGenericVIFDriver,
              self).unplug(instance, vif)

    def unplug_ovs_hybrid(self, instance, vif):
        """UnPlug using hybrid strategy

        Unhook port from OVS, unhook port from bridge, delete
        bridge, and delete both veth devices.
        """
        super(LibvirtGenericVIFDriver,
              self).unplug(instance, vif)

        try:
            br_name = self.get_br_name(vif['id'])
            v1_name, v2_name = self.get_veth_pair_names(vif['id'])

            utils.execute('brctl', 'delif', br_name, v1_name, run_as_root=True)
            utils.execute('ip', 'link', 'set', br_name, 'down',
                          run_as_root=True)
            utils.execute('brctl', 'delbr', br_name, run_as_root=True)

            linux_net.delete_ovs_vif_port(self.get_bridge_name(vif),
                                          v2_name)
        except exception.ProcessExecutionError:
            LOG.exception(_("Failed while unplugging vif"), instance=instance)

    def unplug_ovs(self, instance, vif):
        if self.get_firewall_required():
            self.unplug_ovs_hybrid(instance, vif)
        elif self.has_libvirt_version(LIBVIRT_OVS_VPORT_VERSION):
            self.unplug_ovs_bridge(instance, vif)
        else:
            self.unplug_ovs_ethernet(instance, vif)

    def unplug_802qbg(self, instance, vif):
        super(LibvirtGenericVIFDriver,
              self).unplug(instance, vif)

    def unplug_802qbh(self, instance, vif):
        super(LibvirtGenericVIFDriver,
              self).unplug(instance, vif)

    def unplug(self, instance, vif):
        vif_type = vif['type']

        LOG.debug(_("vif_type=%(vif_type)s instance=%(instance)s "
                    "vif=%(vif)s")
                  % locals())

        if vif_type is None:
            raise exception.NovaException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))

        if vif_type == network_model.VIF_TYPE_BRIDGE:
            self.unplug_bridge(instance, vif)
        elif vif_type == network_model.VIF_TYPE_OVS:
            self.unplug_ovs(instance, vif)
        elif vif_type == network_model.VIF_TYPE_802_QBG:
            self.unplug_802qbg(instance, vif)
        elif vif_type == network_model.VIF_TYPE_802_QBH:
            self.unplug_802qbh(instance, vif)
        else:
            raise exception.NovaException(
                _("Unexpected vif_type=%s") % vif_type)


class LibvirtBridgeDriver(LibvirtGenericVIFDriver):
    """Retained in Grizzly for compatibility with Quantum
       drivers which do not yet report 'vif_type' port binding.
       Will be deprecated in Havana, and removed in Ixxxx."""

    def get_config(self, instance, vif, image_meta):
        return self.get_config_bridge(instance, vif, image_meta)

    def plug(self, instance, vif):
        self.plug_bridge(instance, vif)

    def unplug(self, instance, vif):
        self.unplug_bridge(instance, vif)


class LibvirtOpenVswitchDriver(LibvirtGenericVIFDriver):
    """Retained in Grizzly for compatibility with Quantum
       drivers which do not yet report 'vif_type' port binding.
       Will be deprecated in Havana, and removed in Ixxxx."""

    def get_bridge_name(self, vif):
        return vif['network']['bridge'] or CONF.libvirt_ovs_bridge

    def get_ovs_interfaceid(self, vif):
        return vif.get('ovs_interfaceid') or vif['id']

    def get_config(self, instance, vif, image_meta):
        return self.get_config_ovs_ethernet(instance, vif, image_meta)

    def plug(self, instance, vif):
        self.plug_ovs_ethernet(instance, vif)

    def unplug(self, instance, vif):
        self.unplug_ovs_ethernet(instance, vif)


class LibvirtHybridOVSBridgeDriver(LibvirtGenericVIFDriver):
    """Retained in Grizzly for compatibility with Quantum
       drivers which do not yet report 'vif_type' port binding.
       Will be deprecated in Havana, and removed in Ixxxx."""

    def get_bridge_name(self, vif):
        return vif['network']['bridge'] or CONF.libvirt_ovs_bridge

    def get_ovs_interfaceid(self, vif):
        return vif.get('ovs_interfaceid') or vif['id']

    def get_config(self, instance, vif, image_meta):
        return self.get_config_ovs_hybrid(instance, vif, image_meta)

    def plug(self, instance, vif):
        return self.plug_ovs_hybrid(instance, vif)

    def unplug(self, instance, vif):
        return self.unplug_ovs_hybrid(instance, vif)


class LibvirtOpenVswitchVirtualPortDriver(LibvirtGenericVIFDriver):
    """Retained in Grizzly for compatibility with Quantum
       drivers which do not yet report 'vif_type' port binding.
       Will be deprecated in Havana, and removed in Ixxxx."""

    def get_bridge_name(self, vif):
        return vif['network']['bridge'] or CONF.libvirt_ovs_bridge

    def get_ovs_interfaceid(self, vif):
        return vif.get('ovs_interfaceid') or vif['id']

    def get_config(self, instance, vif, image_meta):
        return self.get_config_ovs_bridge(instance, vif, image_meta)

    def plug(self, instance, vif):
        return self.plug_ovs_bridge(instance, vif)

    def unplug(self, instance, vif):
        return self.unplug_ovs_bridge(instance, vif)


class QuantumLinuxBridgeVIFDriver(LibvirtGenericVIFDriver):
    """Retained in Grizzly for compatibility with Quantum
       drivers which do not yet report 'vif_type' port binding.
       Will be deprecated in Havana, and removed in Ixxxx."""

    def get_bridge_name(self, vif):
        network = vif['network']
        def_bridge = ("brq" + network['id'])[:network_model.NIC_NAME_LEN]
        return network['bridge'] or def_bridge

    def get_config(self, instance, vif, image_meta):
        # In order for libvirt to make use of the bridge name then it has
        # to ensure that the bridge exists
        network = vif.get('network')
        if network and not network.get_meta('should_create_bridge', False):
            vif['network']['meta']['should_create_bridge'] = True
        return self.get_config_bridge(instance, vif, image_meta)

    def plug(self, instance, vif):
        self.plug_bridge(instance, vif)

    def unplug(self, instance, vif):
        self.unplug_bridge(instance, vif)
