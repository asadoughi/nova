# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
#    Copyright 2012 Nicira, Inc
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

from lxml import etree

from nova import exception
from nova.network import model as network_model
from nova.openstack.common import cfg
from nova import test
from nova.tests import fakelibvirt
from nova import utils
from nova.virt.libvirt import config as vconfig
from nova.virt.libvirt import vif
from nova.virt import netutils

CONF = cfg.CONF


class LibvirtVifTestCase(test.TestCase):

    gateway_bridge_4 = network_model.IP(address='10.0.0.1', type='gateway')
    dns_bridge_4 = network_model.IP(address='8.8.8.8', type=None)
    ips_bridge_4 = [network_model.IP(address='101.168.1.9', type=None)]
    subnet_bridge_4 = network_model.Subnet(cidr='101.168.1.0/24',
                                           dns=[dns_bridge_4],
                                           gateway=gateway_bridge_4,
                                           ips=ips_bridge_4,
                                           routes=None,
                                           dhcp_server=
                                               '191.168.1.1')
    gateway_bridge_6 = network_model.IP(address='101:1db9::1', type='gateway')
    subnet_bridge_6 = network_model.Subnet(cidr='101:1db9::/64',
                                           dns=None,
                                           gateway=gateway_bridge_6,
                                           ips=None,
                                           routes=None)

    network_bridge = network_model.Network(id='network-id-xxx-yyy-zzz',
                                           bridge='br0',
                                           label=None,
                                           subnets=[subnet_bridge_4,
                                                    subnet_bridge_6],
                                           bridge_interface='eth0',
                                           vlan=99)

    vif_bridge = network_model.VIF(id='vif-xxx-yyy-zzz',
                                   address='ca:fe:de:ad:be:ef',
                                   network=network_bridge,
                                   type=network_model.VIF_TYPE_BRIDGE,
                                   devname='tap-xxx-yyy-zzz',
                                   ovs_interfaceid=None)

    network_bridge_quantum = network_model.Network(id='network-id-xxx-yyy-zzz',
                                                   bridge=None,
                                                   label=None,
                                                   subnets=[subnet_bridge_4,
                                                            subnet_bridge_6],
                                                   bridge_interface='eth0',
                                                   vlan=99)

    vif_bridge_quantum = network_model.VIF(id='vif-xxx-yyy-zzz',
                                           address='ca:fe:de:ad:be:ef',
                                           network=network_bridge_quantum,
                                           type=None,
                                           devname='tap-xxx-yyy-zzz',
                                           ovs_interfaceid=None)

    network_ovs = network_model.Network(id='network-id-xxx-yyy-zzz',
                                        bridge='br0',
                                        label=None,
                                        subnets=[subnet_bridge_4,
                                                 subnet_bridge_6],
                                        bridge_interface=None,
                                        vlan=99)

    vif_ovs = network_model.VIF(id='vif-xxx-yyy-zzz',
                                address='ca:fe:de:ad:be:ef',
                                network=network_ovs,
                                type=network_model.VIF_TYPE_OVS,
                                devname='tap-xxx-yyy-zzz',
                                ovs_interfaceid='aaa-bbb-ccc')

    vif_ovs_legacy = network_model.VIF(id='vif-xxx-yyy-zzz',
                                       address='ca:fe:de:ad:be:ef',
                                       network=network_ovs,
                                       type=None,
                                       devname=None,
                                       ovs_interfaceid=None)

    vif_none = network_model.VIF(id='vif-xxx-yyy-zzz',
                                 address='ca:fe:de:ad:be:ef',
                                 network=network_bridge,
                                 type=None,
                                 devname='tap-xxx-yyy-zzz',
                                 ovs_interfaceid=None)

    network_8021 = network_model.Network(id='network-id-xxx-yyy-zzz',
                                         bridge=None,
                                         label=None,
                                         subnets=[subnet_bridge_4,
                                                  subnet_bridge_6],
                                         interface='eth0',
                                         vlan=99)
    vif_8021qbh = network_model.VIF(id='vif-xxx-yyy-zzz',
                                    address='ca:fe:de:ad:be:ef',
                                    network=network_8021,
                                    type=network_model.VIF_TYPE_802_QBH,
                                    devname='tap-xxx-yyy-zzz',
                                    ovs_interfaceid=None,
                                    qbh_params=network_model.VIF8021QbhParams(
                                        profileid="xxx-yyy-zzz"))

    vif_8021qbg = network_model.VIF(id='vif-xxx-yyy-zzz',
                                    address='ca:fe:de:ad:be:ef',
                                    network=network_8021,
                                    type=network_model.VIF_TYPE_802_QBG,
                                    devname='tap-xxx-yyy-zzz',
                                    ovs_interfaceid=None,
                                    qbg_params=network_model.VIF8021QbgParams(
                                        managerid="xxx-yyy-zzz",
                                        typeid="aaa-bbb-ccc",
                                        typeidversion="1",
                                        instanceid="ddd-eee-fff"))

    instance = {
        'name': 'instance-name',
        'uuid': 'instance-uuid'
    }

    def setUp(self):
        super(LibvirtVifTestCase, self).setUp()
        self.flags(allow_same_net_traffic=True)
        self.executes = []

        def fake_execute(*cmd, **kwargs):
            self.executes.append(cmd)
            return None, None

        self.stubs.Set(utils, 'execute', fake_execute)

    def _get_instance_xml(self, driver, vif, image_meta=None):
        conf = vconfig.LibvirtConfigGuest()
        conf.virt_type = "qemu"
        conf.name = "fake-name"
        conf.uuid = "fake-uuid"
        conf.memory = 100 * 1024
        conf.vcpus = 4

        nic = driver.get_config(self.instance, vif, image_meta)
        conf.add_device(nic)
        return conf.to_xml()

    def test_multiple_nics(self):
        conf = vconfig.LibvirtConfigGuest()
        conf.virt_type = "qemu"
        conf.name = "fake-name"
        conf.uuid = "fake-uuid"
        conf.memory = 100 * 1024
        conf.vcpus = 4

        # Tests multiple nic configuration and that target_dev is
        # set for each
        nics = [{'net_type': 'bridge',
                 'mac_addr': '00:00:00:00:00:0b',
                 'source_dev': 'b_source_dev',
                 'target_dev': 'b_target_dev'},
                {'net_type': 'ethernet',
                 'mac_addr': '00:00:00:00:00:0e',
                 'source_dev': 'e_source_dev',
                 'target_dev': 'e_target_dev'},
                {'net_type': 'direct',
                 'mac_addr': '00:00:00:00:00:0d',
                 'source_dev': 'd_source_dev',
                 'target_dev': 'd_target_dev'}]

        for nic in nics:
            nic_conf = vconfig.LibvirtConfigGuestInterface()
            nic_conf.net_type = nic['net_type']
            nic_conf.target_dev = nic['target_dev']
            nic_conf.mac_addr = nic['mac_addr']
            nic_conf.source_dev = nic['source_dev']
            conf.add_device(nic_conf)

        xml = conf.to_xml()
        doc = etree.fromstring(xml)
        for nic in nics:
            path = "./devices/interface/[@type='%s']" % nic['net_type']
            node = doc.find(path)
            self.assertEqual(nic['net_type'], node.get("type"))
            self.assertEqual(nic['mac_addr'],
                             node.find("mac").get("address"))
            self.assertEqual(nic['target_dev'],
                             node.find("target").get("dev"))

    def test_model_novirtio(self):
        self.flags(libvirt_use_virtio_for_bridges=False,
                   libvirt_type='kvm')

        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        xml = self._get_instance_xml(d,
                                     self.vif_bridge)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]

        ret = node.findall("model")
        self.assertEqual(len(ret), 0)
        ret = node.findall("driver")
        self.assertEqual(len(ret), 0)

    def test_model_kvm(self):
        self.flags(libvirt_use_virtio_for_bridges=True,
                   libvirt_type='kvm')

        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        xml = self._get_instance_xml(d,
                                     self.vif_bridge)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]

        model = node.find("model").get("type")
        self.assertEqual(model, "virtio")
        ret = node.findall("driver")
        self.assertEqual(len(ret), 0)

    def test_model_kvm_custom(self):
        self.flags(libvirt_use_virtio_for_bridges=True,
                   libvirt_type='kvm')

        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        image_meta = {'properties': {'vif_model': 'e1000'}}
        xml = self._get_instance_xml(d,
                                     self.vif_bridge,
                                     image_meta)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]

        model = node.find("model").get("type")
        self.assertEqual(model, "e1000")
        ret = node.findall("driver")
        self.assertEqual(len(ret), 0)

    def test_model_kvm_bogus(self):
        self.flags(libvirt_use_virtio_for_bridges=True,
                   libvirt_type='kvm')

        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        image_meta = {'properties': {'vif_model': 'acme'}}
        self.assertRaises(exception.UnsupportedHardware,
                          self._get_instance_xml,
                          d,
                          self.vif_bridge,
                          image_meta)

    def test_model_qemu(self):
        self.flags(libvirt_use_virtio_for_bridges=True,
                   libvirt_type='qemu')

        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        xml = self._get_instance_xml(d,
                                     self.vif_bridge)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]

        model = node.find("model").get("type")
        self.assertEqual(model, "virtio")
        driver = node.find("driver").get("name")
        self.assertEqual(driver, "qemu")

    def test_model_xen(self):
        self.flags(libvirt_use_virtio_for_bridges=True,
                   libvirt_type='xen')

        def get_connection():
            return fakelibvirt.Connection("xen:///system",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        xml = self._get_instance_xml(d,
                                     self.vif_bridge)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]

        ret = node.findall("model")
        self.assertEqual(len(ret), 0)
        ret = node.findall("driver")
        self.assertEqual(len(ret), 0)

    def test_generic_driver_none(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        self.assertRaises(exception.NovaException,
                          self._get_instance_xml,
                          d,
                          self.vif_none)

    def _check_bridge_driver(self, d, vif, br_want):
        xml = self._get_instance_xml(d, vif)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(node.get("type"), "bridge")
        br_name = node.find("source").get("bridge")
        self.assertEqual(br_name, br_want)
        mac = node.find("mac").get("address")
        self.assertEqual(mac, self.vif_bridge['address'])

        fw_filter = node.find("filterref")
        net4, mask4 = netutils.get_net_and_mask(self.subnet_bridge_4['cidr'])
        net6, mask6 = netutils.get_net_and_prefixlen(
            self.subnet_bridge_6['cidr'])
        expected = {
            'IP': self.ips_bridge_4[0]['address'],
            'DHCPSERVER': self.subnet_bridge_4.get_meta('dhcp_server'),
            'RASERVER': self.gateway_bridge_6['address'] + '/128',
            'PROJNET': net4,
            'PROJMASK': mask4,
            'PROJNET6': net6,
            'PROJMASK6': mask6,
            }
        for param in fw_filter.findall("parameter"):
            name = param.get('name')
            value = param.get('value')
            self.assertIn(name, expected.keys())
            self.assertEqual(expected[name], value)

    def test_bridge_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtBridgeDriver(get_connection)
        self._check_bridge_driver(d,
                                  self.vif_bridge,
                                  self.vif_bridge['network']['bridge'])

    def test_generic_driver_bridge(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        self._check_bridge_driver(d,
                                  self.vif_bridge,
                                  self.vif_bridge['network']['bridge'])

    def test_quantum_bridge_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.QuantumLinuxBridgeVIFDriver(get_connection)
        br_want = 'brq' + self.vif_bridge_quantum['network']['id']
        br_want = br_want[:network_model.NIC_NAME_LEN]
        self._check_bridge_driver(d,
                                  self.vif_bridge_quantum,
                                  br_want)

    def _check_ovs_ethernet_driver(self, d, vif):
        self.flags(firewall_driver="nova.virt.firewall.NoopFirewallDriver")
        xml = self._get_instance_xml(d, vif)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(node.get("type"), "ethernet")
        dev_name = node.find("target").get("dev")
        self.assertTrue(dev_name.startswith("tap"))
        mac = node.find("mac").get("address")
        self.assertEqual(mac, self.vif_ovs['address'])
        script = node.find("script").get("path")
        self.assertEquals(script, "")

    def test_ovs_ethernet_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False,
                                          9010)
        d = vif.LibvirtOpenVswitchDriver(get_connection)
        d = vif.LibvirtOpenVswitchDriver()
        self._check_ovs_ethernet_driver(d,
                                        self.vif_ovs_legacy)

    def test_ovs_ethernet_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False,
                                          9010)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        self._check_ovs_ethernet_driver(d,
                                        self.vif_ovs)

    def _check_ovs_virtualport_driver(self, d, vif, want_iface_id):
        self.flags(firewall_driver="nova.virt.firewall.NoopFirewallDriver")
        xml = self._get_instance_xml(d, vif)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(node.get("type"), "bridge")

        br_name = node.find("source").get("bridge")
        self.assertEqual(br_name, "br0")
        mac = node.find("mac").get("address")
        self.assertEqual(mac, vif['address'])
        vp = node.find("virtualport")
        self.assertEqual(vp.get("type"), "openvswitch")
        iface_id_found = False
        for p_elem in vp.findall("parameters"):
            iface_id = p_elem.get("interfaceid", None)
            if iface_id:
                self.assertEqual(iface_id, want_iface_id)
                iface_id_found = True

        self.assertTrue(iface_id_found)

    def test_ovs_virtualport_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False,
                                          9011)
        d = vif.LibvirtOpenVswitchVirtualPortDriver(get_connection)
        want_iface_id = 'vif-xxx-yyy-zzz'
        self._check_ovs_virtualport_driver(d,
                                           self.vif_ovs_legacy,
                                           want_iface_id)

    def test_generic_ovs_virtualport_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False,
                                          9011)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        want_iface_id = self.vif_ovs['ovs_interfaceid']
        self._check_ovs_virtualport_driver(d,
                                           self.vif_ovs,
                                           want_iface_id)

    def _check_quantum_hybrid_driver(self, d, vif, br_want):
        self.flags(firewall_driver="nova.virt.firewall.IptablesFirewallDriver")
        xml = self._get_instance_xml(d, vif)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(node.get("type"), "bridge")
        br_name = node.find("source").get("bridge")
        self.assertEqual(br_name, br_want)
        mac = node.find("mac").get("address")
        self.assertEqual(mac, vif['address'])

    def test_quantum_hybrid_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        br_want = "qbr" + self.vif_ovs['id']
        br_want = br_want[:network_model.NIC_NAME_LEN]
        d = vif.LibvirtHybridOVSBridgeDriver(get_connection)
        self._check_quantum_hybrid_driver(d,
                                          self.vif_ovs_legacy,
                                          br_want)

    def test_generic_hybrid_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        br_want = "qbr" + self.vif_ovs['id']
        br_want = br_want[:network_model.NIC_NAME_LEN]
        self._check_quantum_hybrid_driver(d,
                                          self.vif_ovs,
                                          br_want)

    def test_generic_8021qbh_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        xml = self._get_instance_xml(d,
                                     self.vif_8021qbh)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(node.get("type"), "direct")

        br_name = node.find("source").get("dev")
        self.assertEqual(br_name, "eth0")
        mac = node.find("mac").get("address")
        self.assertEqual(mac, self.vif_8021qbh['address'])
        vp = node.find("virtualport")
        self.assertEqual(vp.get("type"), "802.1Qbh")
        profile_id_found = False
        for p_elem in vp.findall("parameters"):
            wantparams = self.vif_8021qbh['qbh_params']
            profile_id = p_elem.get("profileid", None)
            if profile_id:
                self.assertEqual(profile_id,
                                 wantparams['profileid'])
                profile_id_found = True

        self.assertTrue(profile_id_found)

    def test_generic_8021qbg_driver(self):
        def get_connection():
            return fakelibvirt.Connection("qemu:///session",
                                          False)
        d = vif.LibvirtGenericVIFDriver(get_connection)
        xml = self._get_instance_xml(d,
                                     self.vif_8021qbg)

        doc = etree.fromstring(xml)
        ret = doc.findall('./devices/interface')
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(node.get("type"), "direct")

        br_name = node.find("source").get("dev")
        self.assertEqual(br_name, "eth0")
        mac = node.find("mac").get("address")
        self.assertEqual(mac, self.vif_8021qbg['address'])
        vp = node.find("virtualport")
        self.assertEqual(vp.get("type"), "802.1Qbg")
        manager_id_found = False
        type_id_found = False
        typeversion_id_found = False
        instance_id_found = False
        for p_elem in vp.findall("parameters"):
            wantparams = self.vif_8021qbg['qbg_params']
            manager_id = p_elem.get("managerid", None)
            type_id = p_elem.get("typeid", None)
            typeversion_id = p_elem.get("typeidversion", None)
            instance_id = p_elem.get("instanceid", None)
            if manager_id:
                self.assertEqual(manager_id,
                                 wantparams['managerid'])
                manager_id_found = True
            if type_id:
                self.assertEqual(type_id,
                                 wantparams['typeid'])
                type_id_found = True
            if typeversion_id:
                self.assertEqual(typeversion_id,
                                 wantparams['typeidversion'])
                typeversion_id_found = True
            if instance_id:
                self.assertEqual(instance_id,
                                 wantparams['instanceid'])
                instance_id_found = True

        self.assertTrue(manager_id_found)
        self.assertTrue(type_id_found)
        self.assertTrue(typeversion_id_found)
        self.assertTrue(instance_id_found)
