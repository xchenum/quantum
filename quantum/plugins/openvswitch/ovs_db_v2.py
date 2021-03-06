# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright 2011 Nicira Networks, Inc.
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
# @author: Aaron Rosen, Nicira Networks, Inc.
# @author: Bob Kukura, Red Hat, Inc.

import logging

from sqlalchemy.orm import exc

from quantum.common import exceptions as q_exc
import quantum.db.api as db
from quantum.openstack.common import cfg
from quantum.plugins.openvswitch import ovs_models_v2

LOG = logging.getLogger(__name__)


def get_vlans():
    session = db.get_session()
    try:
        bindings = (session.query(ovs_models_v2.VlanBinding).
                    all())
    except exc.NoResultFound:
        return []
    return [(binding.vlan_id, binding.network_id) for binding in bindings]


def get_vlan(net_id):
    session = db.get_session()
    try:
        binding = (session.query(ovs_models_v2.VlanBinding).
                   filter_by(network_id=net_id).
                   one())
    except exc.NoResultFound:
        return
    return binding.vlan_id


def add_vlan_binding(vlan_id, net_id):
    session = db.get_session()
    binding = ovs_models_v2.VlanBinding(vlan_id, net_id)
    session.add(binding)
    session.flush()
    return binding


def remove_vlan_binding(net_id):
    session = db.get_session()
    try:
        binding = (session.query(ovs_models_v2.VlanBinding).
                   filter_by(network_id=net_id).
                   one())
        session.delete(binding)
    except exc.NoResultFound:
        pass
    session.flush()


def update_vlan_id_pool():
    """Update vlan_ids based on current configuration."""

    # determine current dynamically-allocated range
    vlans = set(xrange(cfg.CONF.OVS.vlan_min,
                       cfg.CONF.OVS.vlan_max + 1))

    session = db.get_session()
    with session.begin(subtransactions=True):
        # remove unused vlan_ids outside current range
        try:
            records = (session.query(ovs_models_v2.VlanID).
                       all())
            for record in records:
                try:
                    vlans.remove(record.vlan_id)
                except KeyError:
                    if not record.vlan_used:
                        LOG.debug("removing vlan %s from pool"
                                  % record.vlan_id)
                        session.delete(record)
        except exc.NoResultFound:
            pass

        # add missing vlan_ids
        for vlan in vlans:
            record = ovs_models_v2.VlanID(vlan)
            session.add(record)


def get_vlan_id(vlan_id):
    """Get state of specified vlan"""

    session = db.get_session()
    try:
        record = (session.query(ovs_models_v2.VlanID).
                  filter_by(vlan_id=vlan_id).
                  one())
        return record
    except exc.NoResultFound:
        return None


def reserve_vlan_id():
    """Reserve an unused vlan_id"""

    session = db.get_session()
    with session.begin(subtransactions=True):
        record = (session.query(ovs_models_v2.VlanID).
                  filter_by(vlan_used=False).
                  first())
        if not record:
            raise q_exc.NoNetworkAvailable()
        LOG.debug("reserving vlan %s from pool" % record.vlan_id)
        record.vlan_used = True
    return record.vlan_id


def reserve_specific_vlan_id(vlan_id):
    """Reserve a specific vlan_id"""

    if vlan_id < 1 or vlan_id > 4094:
        msg = _("Specified VLAN %s outside legal range (1-4094)") % vlan_id
        raise q_exc.InvalidInput(error_message=msg)

    session = db.get_session()
    with session.begin(subtransactions=True):
        try:
            record = (session.query(ovs_models_v2.VlanID).
                      filter_by(vlan_id=vlan_id).
                      one())
            if record.vlan_used:
                raise q_exc.VlanIdInUse(vlan_id=vlan_id)
            LOG.debug("reserving specific vlan %s from pool" % vlan_id)
            record.vlan_used = True
        except exc.NoResultFound:
            LOG.debug("reserving specific vlan %s outside pool" % vlan_id)
            record = ovs_models_v2.VlanID(vlan_id)
            record.vlan_used = True
            session.add(record)


def release_vlan_id(vlan_id):
    """Set the vlan state to be unused, and delete if not in range"""

    session = db.get_session()
    with session.begin(subtransactions=True):
        try:
            record = (session.query(ovs_models_v2.VlanID).
                      filter_by(vlan_id=vlan_id).
                      one())
            record.vlan_used = False
            if (vlan_id >= cfg.CONF.OVS.vlan_min and
                vlan_id <= cfg.CONF.OVS.vlan_max):
                LOG.debug("releasing vlan %s to pool" % vlan_id)
            else:
                LOG.debug("removing vlan %s outside pool" % vlan_id)
                session.delete(record)
        except exc.NoResultFound:
            LOG.error("vlan id %s not found in release_vlan_id" % vlan_id)
