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

from quantum.agent.linux import utils
from quantum.common import exceptions


class SubProcessBase(object):
    def __init__(self, root_helper=None, namespace=None):
        self.root_helper = root_helper
        self.namespace = namespace

    def _run(self, options, command, args):
        if self.namespace:
            return self._as_root(options, command, args)
        else:
            return self._execute(options, command, args)

    def _as_root(self, options, command, args):
        if not self.root_helper:
            raise exceptions.SudoRequired()
        return self._execute(options,
                             command,
                             args,
                             self.root_helper,
                             self.namespace)

    @classmethod
    def _execute(cls, options, command, args, root_helper=None,
                 namespace=None):
        opt_list = ['-%s' % o for o in options]
        if namespace:
            ip_cmd = ['ip', 'netns', 'exec', namespace, 'ip']
        else:
            ip_cmd = ['ip']
        return utils.execute(ip_cmd + opt_list + [command] + list(args),
                             root_helper=root_helper)


class IPWrapper(SubProcessBase):
    def __init__(self, root_helper=None, namespace=None):
        super(IPWrapper, self).__init__(root_helper=root_helper,
                                        namespace=namespace)
        self.netns = IpNetnsCommand(self)

    def device(self, name):
        return IPDevice(name, self.root_helper, self.namespace)

    def get_devices(self):
        retval = []
        output = self._execute('o', 'link', ('list',),
                               self.root_helper, self.namespace)
        for line in output.split('\n'):
            if '<' not in line:
                continue
            tokens = line.split(':', 2)
            if len(tokens) >= 3:
                retval.append(IPDevice(tokens[1].strip(),
                                       self.root_helper,
                                       self.namespace))
        return retval

    def add_tuntap(self, name, mode='tap'):
        self._as_root('', 'tuntap', ('add', name, 'mode', mode))
        return IPDevice(name, self.root_helper, self.namespace)

    def add_veth(self, name1, name2):
        self._as_root('', 'link',
                      ('add', name1, 'type', 'veth', 'peer', 'name', name2))

        return (IPDevice(name1, self.root_helper, self.namespace),
                IPDevice(name2, self.root_helper, self.namespace))

    def ensure_namespace(self, name):
        if not self.netns.exists(name):
            ip = self.netns.add(name)
            lo = ip.device('lo')
            lo.link.set_up()
        else:
            ip = IP(self.root_helper, name)
        return ip

    def add_device_to_namespace(self, device):
        if self.namespace:
            device.link.set_netns(self.namespace)

    @classmethod
    def get_namespaces(cls, root_helper):
        output = cls._execute('netns', ('list',), root_helper=root_helper)
        return [l.strip() for l in output.split('\n')]


class IPDevice(SubProcessBase):
    def __init__(self, name, root_helper=None, namespace=None):
        super(IPDevice, self).__init__(root_helper=root_helper,
                                       namespace=namespace)
        self.name = name
        self.link = IpLinkCommand(self)
        self.addr = IpAddrCommand(self)

    def __eq__(self, other):
        return (other is not None and self.name == other.name
                and self.namespace == other.namespace)

    def __str__(self):
        return self.name


class IpCommandBase(object):
    COMMAND = ''

    def __init__(self, parent):
        self._parent = parent

    def _run(self, *args, **kwargs):
        return self._parent._run(kwargs.get('options', []), self.COMMAND, args)

    def _as_root(self, *args, **kwargs):
        return self._parent._as_root(kwargs.get('options', []),
                                     self.COMMAND,
                                     args)


class IpDeviceCommandBase(IpCommandBase):
    @property
    def name(self):
        return self._parent.name


class IpLinkCommand(IpDeviceCommandBase):
    COMMAND = 'link'

    def set_address(self, mac_address):
        self._as_root('set', self.name, 'address', mac_address)

    def set_mtu(self, mtu_size):
        self._as_root('set', self.name, 'mtu', mtu_size)

    def set_up(self):
        self._as_root('set', self.name, 'up')

    def set_down(self):
        self._as_root('set', self.name, 'down')

    def set_netns(self, namespace):
        self._as_root('set', self.name, 'netns', namespace)
        self._parent.namespace = namespace

    def set_name(self, name):
        self._as_root('set', self.name, 'name', name)
        self._parent.name = name

    def delete(self):
        self._as_root('delete', self.name)

    @property
    def address(self):
        return self.attributes.get('link/ether')

    @property
    def state(self):
        return self.attributes.get('state')

    @property
    def mtu(self):
        return self.attributes.get('mtu')

    @property
    def qdisc(self):
        return self.attributes.get('qdisc')

    @property
    def qlen(self):
        return self.attributes.get('qlen')

    @property
    def attributes(self):
        return self._parse_line(self._run('show', self.name, options='o'))

    def _parse_line(self, value):
        device_name, settings = value.replace("\\", '').split('>', 1)

        tokens = settings.split()
        keys = tokens[::2]
        values = [int(v) if v.isdigit() else v for v in tokens[1::2]]

        retval = dict(zip(keys, values))
        return retval


class IpAddrCommand(IpDeviceCommandBase):
    COMMAND = 'addr'

    def add(self, ip_version, cidr, broadcast, scope='global'):
        self._as_root('add',
                      cidr,
                      'brd',
                      broadcast,
                      'scope',
                      scope,
                      'dev',
                      self.name,
                      options=[ip_version])

    def delete(self, ip_version, cidr):
        self._as_root('del',
                      cidr,
                      'dev',
                      self.name,
                      options=[ip_version])

    def flush(self):
        self._as_root('flush', self.name)

    def list(self, scope=None, to=None, filters=[]):
        retval = []

        if scope:
            filters += ['scope', scope]
        if to:
            filters += ['to', to]

        for line in self._run('show', self.name, *filters).split('\n'):
            line = line.strip()
            if not line.startswith('inet'):
                continue
            parts = line.split()
            if parts[0] == 'inet6':
                version = 6
                scope = parts[3]
            else:
                version = 4
                scope = parts[5]

            retval.append(dict(cidr=parts[1],
                               scope=scope,
                               ip_version=version,
                               dynamic=('dynamic' == parts[-1])))
        return retval


class IpNetnsCommand(IpCommandBase):
    COMMAND = 'netns'

    def add(self, name):
        self._as_root('add', name)
        return IPWrapper(self._parent.root_helper, name)

    def delete(self, name):
        self._as_root('delete', name)

    def execute(self, cmds):
        if not self._parent.root_helper:
            raise exceptions.SudoRequired()
        elif not self._parent.namespace:
            raise Exception(_('No namespace defined for parent'))
        else:
            return utils.execute(
                ['ip', 'netns', 'exec', self._parent.namespace] + list(cmds),
                root_helper=self._parent.root_helper)

    def exists(self, name):
        output = self._as_root('list', options='o')

        for line in output.split('\n'):
            if name == line.strip():
                return True
        return False


def device_exists(device_name, root_helper=None, namespace=None):
    try:
        address = IPDevice(device_name, root_helper, namespace).link.address
    except RuntimeError:
        return False
    return True
