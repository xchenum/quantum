# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack LLC
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

import unittest

import mock

from quantum.agent.common import config
from quantum.agent.linux import interface
from quantum.agent.linux import ip_lib
from quantum.agent.linux import utils
from quantum.openstack.common import cfg


class BaseChild(interface.LinuxInterfaceDriver):
    def plug(*args):
        pass

    def unplug(*args):
        pass


class FakeNetwork:
    id = '12345678-1234-5678-90ab-ba0987654321'


class FakeSubnet:
    cidr = '192.168.1.1/24'


class FakeAllocation:
    subnet = FakeSubnet()
    ip_address = '192.168.1.2'
    ip_version = 4


class FakePort:
    id = 'abcdef01-1234-5678-90ab-ba0987654321'
    fixed_ips = [FakeAllocation]
    device_id = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
    network = FakeNetwork()


class TestBase(unittest.TestCase):
    def setUp(self):
        root_helper_opt = [
            cfg.StrOpt('root_helper', default='sudo'),
        ]
        self.conf = config.setup_conf()
        self.conf.register_opts(interface.OPTS)
        self.conf.register_opts(root_helper_opt)
        self.ip_dev_p = mock.patch.object(ip_lib, 'IPDevice')
        self.ip_dev = self.ip_dev_p.start()
        self.ip_p = mock.patch.object(ip_lib, 'IPWrapper')
        self.ip = self.ip_p.start()
        self.device_exists_p = mock.patch.object(ip_lib, 'device_exists')
        self.device_exists = self.device_exists_p.start()

    def tearDown(self):
        # sometimes a test may turn this off
        try:
            self.device_exists_p.stop()
        except RuntimeError, e:
            pass
        self.ip_dev_p.stop()
        self.ip_p.stop()


class TestABCDriver(TestBase):
    def test_get_device_name(self):
        bc = BaseChild(self.conf)
        device_name = bc.get_device_name(FakePort())
        self.assertEqual('tapabcdef01-12', device_name)

    def test_l3_init(self):
        addresses = [dict(ip_version=4, scope='global',
                          dynamic=False, cidr='172.16.77.240/24')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)

        bc = BaseChild(self.conf)
        bc.init_l3(FakePort(), 'tap0')
        self.ip_dev.assert_has_calls(
            [mock.call('tap0', 'sudo'),
             mock.call().addr.list(scope='global', filters=['permanent']),
             mock.call().addr.add(4, '192.168.1.2/24', '192.168.1.255'),
             mock.call().addr.delete(4, '172.16.77.240/24')])


class TestOVSInterfaceDriver(TestBase):
    def test_plug(self, additional_expectation=[]):
        def device_exists(dev, root_helper=None, namespace=None):
            return dev == 'br-int'

        vsctl_cmd = ['ovs-vsctl', '--', '--may-exist', 'add-port',
                     'br-int', 'tap0', '--', 'set', 'Interface', 'tap0',
                     'type=internal', '--', 'set', 'Interface', 'tap0',
                     'external-ids:iface-id=port-1234', '--', 'set',
                     'Interface', 'tap0',
                     'external-ids:iface-status=active', '--', 'set',
                     'Interface', 'tap0',
                     'external-ids:attached-mac=aa:bb:cc:dd:ee:ff']

        with mock.patch.object(utils, 'execute') as execute:
            ovs = interface.OVSInterfaceDriver(self.conf)
            self.device_exists.side_effect = device_exists
            ovs.plug('01234567-1234-1234-99',
                     'port-1234',
                     'tap0',
                     'aa:bb:cc:dd:ee:ff')
            execute.assert_called_once_with(vsctl_cmd, 'sudo')

        expected = [mock.call('sudo'),
                    mock.call().device('tap0'),
                    mock.call().device().link.set_address('aa:bb:cc:dd:ee:ff')]
        expected.extend(additional_expectation)
        expected.extend([mock.call().device().link.set_up()])

        self.ip.assert_has_calls(expected)

    def test_plug_mtu(self):
        self.conf.set_override('network_device_mtu', 9000)
        self.test_plug([mock.call().device().link.set_mtu(9000)])

    def test_unplug(self):
        with mock.patch('quantum.agent.linux.ovs_lib.OVSBridge') as ovs_br:
            ovs = interface.OVSInterfaceDriver(self.conf)
            ovs.unplug('tap0')
            ovs_br.assert_has_calls([mock.call('br-int', 'sudo'),
                                     mock.call().delete_port('tap0')])


class TestBridgeInterfaceDriver(TestBase):
    def test_get_device_name(self):
        br = interface.BridgeInterfaceDriver(self.conf)
        device_name = br.get_device_name(FakePort())
        self.assertEqual('dhcabcdef01-12', device_name)

    def test_plug(self):
        def device_exists(device, root_helper=None, namespace=None):
            return device.startswith('brq')

        root_veth = mock.Mock()
        ns_veth = mock.Mock()

        self.ip().add_veth = mock.Mock(return_value=(root_veth, ns_veth))

        expected = [mock.call(c, 'sudo') for c in [
            ['ip', 'tuntap', 'add', 'tap0', 'mode', 'tap'],
            ['ip', 'link', 'set', 'tap0', 'address', 'aa:bb:cc:dd:ee:ff'],
            ['ip', 'link', 'set', 'tap0', 'up']]
        ]

        self.device_exists.side_effect = device_exists
        br = interface.BridgeInterfaceDriver(self.conf)
        br.plug('01234567-1234-1234-99',
                'port-1234',
                'dhc0',
                'aa:bb:cc:dd:ee:ff')

        self.ip.assert_has_calls(
            [mock.call(),
             mock.call('sudo'),
             mock.call().add_veth('tap0', 'dhc0')])

        root_veth.assert_has_calls([mock.call.link.set_up()])
        ns_veth.assert_has_calls([mock.call.link.set_up()])

    def test_plug_dev_exists(self):
        self.device_exists.return_value = True
        with mock.patch('quantum.agent.linux.interface.LOG.warn') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.plug('01234567-1234-1234-99',
                    'port-1234',
                    'tap0',
                    'aa:bb:cc:dd:ee:ff')
            self.ip_dev.assert_has_calls([])
            self.assertEquals(log.call_count, 1)

    def test_unplug(self):
        self.device_exists.return_value = True
        with mock.patch('quantum.agent.linux.interface.LOG.debug') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.unplug('tap0')
            log.assert_called_once()
        self.execute.assert_has_calls(
            [mock.call(['ip', 'link', 'delete', 'tap0'], 'sudo')])

    def test_unplug_no_device(self):
        self.device_exists.return_value = False
        self.ip_dev().link.delete.side_effect = RuntimeError
        with mock.patch('quantum.agent.linux.interface.LOG') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.unplug('tap0')
            [mock.call(), mock.call('tap0', 'sudo'), mock.call().link.delete()]
            self.assertEqual(log.error.call_count, 1)

    def test_unplug(self):
        self.device_exists.return_value = True
        with mock.patch('quantum.agent.linux.interface.LOG.debug') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.unplug('tap0')
            log.assert_called_once()

        self.ip_dev.assert_has_calls([mock.call('tap0', 'sudo'),
                                      mock.call().link.delete()])
