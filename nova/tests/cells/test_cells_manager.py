# Copyright (c) 2012 Rackspace Hosting
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
"""
Tests For CellsManager
"""
import copy
import datetime

from oslo.config import cfg

from nova.cells import messaging
from nova.cells import utils as cells_utils
from nova import context
from nova.openstack.common import rpc
from nova.openstack.common import timeutils
from nova import test
from nova.tests.cells import fakes

CONF = cfg.CONF
CONF.import_opt('compute_topic', 'nova.compute.rpcapi')


FAKE_COMPUTE_NODES = [dict(id=1), dict(id=2)]
FAKE_SERVICES = [dict(id=1, host='host1',
                      compute_node=[FAKE_COMPUTE_NODES[0]]),
                 dict(id=2, host='host2',
                      compute_node=[FAKE_COMPUTE_NODES[1]]),
                 dict(id=3, host='host3', compute_node=[])]
FAKE_TASK_LOGS = [dict(id=1, host='host1'),
                  dict(id=2, host='host2')]


class CellsManagerClassTestCase(test.TestCase):
    """Test case for CellsManager class."""

    def setUp(self):
        super(CellsManagerClassTestCase, self).setUp()
        fakes.init(self)
        # pick a child cell to use for tests.
        self.our_cell = 'grandchild-cell1'
        self.cells_manager = fakes.get_cells_manager(self.our_cell)
        self.msg_runner = self.cells_manager.msg_runner
        self.driver = self.cells_manager.driver
        self.ctxt = 'fake_context'

    def _get_fake_response(self, raw_response=None, exc=False):
        if exc:
            return messaging.Response('fake', test.TestingException(),
                                      True)
        if raw_response is None:
            raw_response = 'fake-response'
        return messaging.Response('fake', raw_response, False)

    def test_get_cell_info_for_neighbors(self):
        self.mox.StubOutWithMock(self.cells_manager.state_manager,
                'get_cell_info_for_neighbors')
        self.cells_manager.state_manager.get_cell_info_for_neighbors()
        self.mox.ReplayAll()
        self.cells_manager.get_cell_info_for_neighbors(self.ctxt)

    def test_post_start_hook_child_cell(self):
        self.mox.StubOutWithMock(self.driver, 'start_consumers')
        self.mox.StubOutWithMock(context, 'get_admin_context')
        self.mox.StubOutWithMock(self.cells_manager, '_update_our_parents')

        self.driver.start_consumers(self.msg_runner)
        context.get_admin_context().AndReturn(self.ctxt)
        self.cells_manager._update_our_parents(self.ctxt)
        self.mox.ReplayAll()
        self.cells_manager.post_start_hook()

    def test_post_start_hook_middle_cell(self):
        cells_manager = fakes.get_cells_manager('child-cell2')
        msg_runner = cells_manager.msg_runner
        driver = cells_manager.driver

        self.mox.StubOutWithMock(driver, 'start_consumers')
        self.mox.StubOutWithMock(context, 'get_admin_context')
        self.mox.StubOutWithMock(msg_runner,
                                 'ask_children_for_capabilities')
        self.mox.StubOutWithMock(msg_runner,
                                 'ask_children_for_capacities')

        driver.start_consumers(msg_runner)
        context.get_admin_context().AndReturn(self.ctxt)
        msg_runner.ask_children_for_capabilities(self.ctxt)
        msg_runner.ask_children_for_capacities(self.ctxt)
        self.mox.ReplayAll()
        cells_manager.post_start_hook()

    def test_update_our_parents(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'tell_parents_our_capabilities')
        self.mox.StubOutWithMock(self.msg_runner,
                                 'tell_parents_our_capacities')

        self.msg_runner.tell_parents_our_capabilities(self.ctxt)
        self.msg_runner.tell_parents_our_capacities(self.ctxt)
        self.mox.ReplayAll()
        self.cells_manager._update_our_parents(self.ctxt)

    def test_schedule_run_instance(self):
        host_sched_kwargs = 'fake_host_sched_kwargs_silently_passed'
        self.mox.StubOutWithMock(self.msg_runner, 'schedule_run_instance')
        our_cell = self.msg_runner.state_manager.get_my_state()
        self.msg_runner.schedule_run_instance(self.ctxt, our_cell,
                                              host_sched_kwargs)
        self.mox.ReplayAll()
        self.cells_manager.schedule_run_instance(self.ctxt,
                host_sched_kwargs=host_sched_kwargs)

    def test_run_compute_api_method(self):
        # Args should just be silently passed through
        cell_name = 'fake-cell-name'
        method_info = 'fake-method-info'

        self.mox.StubOutWithMock(self.msg_runner,
                                 'run_compute_api_method')
        fake_response = self._get_fake_response()
        self.msg_runner.run_compute_api_method(self.ctxt,
                                               cell_name,
                                               method_info,
                                               True).AndReturn(fake_response)
        self.mox.ReplayAll()
        response = self.cells_manager.run_compute_api_method(
                self.ctxt, cell_name=cell_name, method_info=method_info,
                call=True)
        self.assertEqual('fake-response', response)

    def test_instance_update_at_top(self):
        self.mox.StubOutWithMock(self.msg_runner, 'instance_update_at_top')
        self.msg_runner.instance_update_at_top(self.ctxt, 'fake-instance')
        self.mox.ReplayAll()
        self.cells_manager.instance_update_at_top(self.ctxt,
                                                  instance='fake-instance')

    def test_instance_destroy_at_top(self):
        self.mox.StubOutWithMock(self.msg_runner, 'instance_destroy_at_top')
        self.msg_runner.instance_destroy_at_top(self.ctxt, 'fake-instance')
        self.mox.ReplayAll()
        self.cells_manager.instance_destroy_at_top(self.ctxt,
                                                  instance='fake-instance')

    def test_instance_delete_everywhere(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'instance_delete_everywhere')
        self.msg_runner.instance_delete_everywhere(self.ctxt,
                                                   'fake-instance',
                                                   'fake-type')
        self.mox.ReplayAll()
        self.cells_manager.instance_delete_everywhere(
                self.ctxt, instance='fake-instance',
                delete_type='fake-type')

    def test_instance_fault_create_at_top(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'instance_fault_create_at_top')
        self.msg_runner.instance_fault_create_at_top(self.ctxt,
                                                     'fake-fault')
        self.mox.ReplayAll()
        self.cells_manager.instance_fault_create_at_top(
                self.ctxt, instance_fault='fake-fault')

    def test_bw_usage_update_at_top(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'bw_usage_update_at_top')
        self.msg_runner.bw_usage_update_at_top(self.ctxt,
                                               'fake-bw-info')
        self.mox.ReplayAll()
        self.cells_manager.bw_usage_update_at_top(
                self.ctxt, bw_update_info='fake-bw-info')

    def test_heal_instances(self):
        self.flags(instance_updated_at_threshold=1000,
                   instance_update_num_instances=2,
                   group='cells')

        fake_context = context.RequestContext('fake', 'fake')
        stalled_time = timeutils.utcnow()
        updated_since = stalled_time - datetime.timedelta(seconds=1000)

        def utcnow():
            return stalled_time

        call_info = {'get_instances': 0, 'sync_instances': []}

        instances = ['instance1', 'instance2', 'instance3']

        def get_instances_to_sync(context, **kwargs):
            self.assertEqual(context, fake_context)
            call_info['shuffle'] = kwargs.get('shuffle')
            call_info['project_id'] = kwargs.get('project_id')
            call_info['updated_since'] = kwargs.get('updated_since')
            call_info['get_instances'] += 1
            return iter(instances)

        def instance_get_by_uuid(context, uuid):
            return instances[int(uuid[-1]) - 1]

        def sync_instance(context, instance):
            self.assertEqual(context, fake_context)
            call_info['sync_instances'].append(instance)

        self.stubs.Set(cells_utils, 'get_instances_to_sync',
                get_instances_to_sync)
        self.stubs.Set(self.cells_manager.db, 'instance_get_by_uuid',
                instance_get_by_uuid)
        self.stubs.Set(self.cells_manager, '_sync_instance',
                sync_instance)
        self.stubs.Set(timeutils, 'utcnow', utcnow)

        self.cells_manager._heal_instances(fake_context)
        self.assertEqual(call_info['shuffle'], True)
        self.assertEqual(call_info['project_id'], None)
        self.assertEqual(call_info['updated_since'], updated_since)
        self.assertEqual(call_info['get_instances'], 1)
        # Only first 2
        self.assertEqual(call_info['sync_instances'],
                instances[:2])

        call_info['sync_instances'] = []
        self.cells_manager._heal_instances(fake_context)
        self.assertEqual(call_info['shuffle'], True)
        self.assertEqual(call_info['project_id'], None)
        self.assertEqual(call_info['updated_since'], updated_since)
        self.assertEqual(call_info['get_instances'], 2)
        # Now the last 1 and the first 1
        self.assertEqual(call_info['sync_instances'],
                [instances[-1], instances[0]])

    def test_sync_instances(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'sync_instances')
        self.msg_runner.sync_instances(self.ctxt, 'fake-project',
                                       'fake-time', 'fake-deleted')
        self.mox.ReplayAll()
        self.cells_manager.sync_instances(self.ctxt,
                                          project_id='fake-project',
                                          updated_since='fake-time',
                                          deleted='fake-deleted')

    def test_service_get_all(self):
        responses = []
        expected_response = []
        # 3 cells... so 3 responses.  Each response is a list of services.
        # Manager should turn these into a single list of responses.
        for i in xrange(3):
            cell_name = 'path!to!cell%i' % i
            services = []
            for service in FAKE_SERVICES:
                services.append(copy.deepcopy(service))
                expected_service = copy.deepcopy(service)
                cells_utils.add_cell_to_service(expected_service, cell_name)
                expected_response.append(expected_service)
            response = messaging.Response(cell_name, services, False)
            responses.append(response)

        self.mox.StubOutWithMock(self.msg_runner,
                                 'service_get_all')
        self.msg_runner.service_get_all(self.ctxt,
                                        'fake-filters').AndReturn(responses)
        self.mox.ReplayAll()
        response = self.cells_manager.service_get_all(self.ctxt,
                                                      filters='fake-filters')
        self.assertEqual(expected_response, response)

    def test_service_get_by_compute_host(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'service_get_by_compute_host')
        fake_cell = 'fake-cell'
        fake_response = messaging.Response(fake_cell, FAKE_SERVICES[0],
                                           False)
        expected_response = copy.deepcopy(FAKE_SERVICES[0])
        cells_utils.add_cell_to_service(expected_response, fake_cell)

        cell_and_host = cells_utils.cell_with_item('fake-cell', 'fake-host')
        self.msg_runner.service_get_by_compute_host(self.ctxt,
                fake_cell, 'fake-host').AndReturn(fake_response)
        self.mox.ReplayAll()
        response = self.cells_manager.service_get_by_compute_host(self.ctxt,
                host_name=cell_and_host)
        self.assertEqual(expected_response, response)

    def test_proxy_rpc_to_manager(self):
        self.mox.StubOutWithMock(self.msg_runner,
                                 'proxy_rpc_to_manager')
        fake_response = self._get_fake_response()
        cell_and_host = cells_utils.cell_with_item('fake-cell', 'fake-host')
        topic = rpc.queue_get_for(self.ctxt, CONF.compute_topic,
                                  cell_and_host)
        self.msg_runner.proxy_rpc_to_manager(self.ctxt, 'fake-cell',
                'fake-host', topic, 'fake-rpc-msg',
                True, -1).AndReturn(fake_response)
        self.mox.ReplayAll()
        response = self.cells_manager.proxy_rpc_to_manager(self.ctxt,
                topic=topic, rpc_message='fake-rpc-msg', call=True,
                timeout=-1)
        self.assertEqual('fake-response', response)

    def _build_task_log_responses(self, num):
        responses = []
        expected_response = []
        # 3 cells... so 3 responses.  Each response is a list of task log
        # entries. Manager should turn these into a single list of
        # task log entries.
        for i in xrange(num):
            cell_name = 'path!to!cell%i' % i
            task_logs = []
            for task_log in FAKE_TASK_LOGS:
                task_logs.append(copy.deepcopy(task_log))
                expected_task_log = copy.deepcopy(task_log)
                cells_utils.add_cell_to_task_log(expected_task_log,
                                                 cell_name)
                expected_response.append(expected_task_log)
            response = messaging.Response(cell_name, task_logs, False)
            responses.append(response)
        return expected_response, responses

    def test_task_log_get_all(self):
        expected_response, responses = self._build_task_log_responses(3)
        self.mox.StubOutWithMock(self.msg_runner,
                                 'task_log_get_all')
        self.msg_runner.task_log_get_all(self.ctxt, None,
                'fake-name', 'fake-begin',
                'fake-end', host=None, state=None).AndReturn(responses)
        self.mox.ReplayAll()
        response = self.cells_manager.task_log_get_all(self.ctxt,
                task_name='fake-name',
                period_beginning='fake-begin', period_ending='fake-end')
        self.assertEqual(expected_response, response)

    def test_task_log_get_all_with_filters(self):
        expected_response, responses = self._build_task_log_responses(1)
        cell_and_host = cells_utils.cell_with_item('fake-cell', 'fake-host')
        self.mox.StubOutWithMock(self.msg_runner,
                                 'task_log_get_all')
        self.msg_runner.task_log_get_all(self.ctxt, 'fake-cell',
                'fake-name', 'fake-begin', 'fake-end', host='fake-host',
                state='fake-state').AndReturn(responses)
        self.mox.ReplayAll()
        response = self.cells_manager.task_log_get_all(self.ctxt,
                task_name='fake-name',
                period_beginning='fake-begin', period_ending='fake-end',
                host=cell_and_host, state='fake-state')
        self.assertEqual(expected_response, response)

    def test_task_log_get_all_with_cell_but_no_host_filters(self):
        expected_response, responses = self._build_task_log_responses(1)
        # Host filter only has cell name.
        cell_and_host = 'fake-cell'
        self.mox.StubOutWithMock(self.msg_runner,
                                 'task_log_get_all')
        self.msg_runner.task_log_get_all(self.ctxt, 'fake-cell',
                'fake-name', 'fake-begin', 'fake-end', host=None,
                state='fake-state').AndReturn(responses)
        self.mox.ReplayAll()
        response = self.cells_manager.task_log_get_all(self.ctxt,
                task_name='fake-name',
                period_beginning='fake-begin', period_ending='fake-end',
                host=cell_and_host, state='fake-state')
        self.assertEqual(expected_response, response)

    def test_compute_node_get_all(self):
        responses = []
        expected_response = []
        # 3 cells... so 3 responses.  Each response is a list of computes.
        # Manager should turn these into a single list of responses.
        for i in xrange(3):
            cell_name = 'path!to!cell%i' % i
            compute_nodes = []
            for compute_node in FAKE_COMPUTE_NODES:
                compute_nodes.append(copy.deepcopy(compute_node))
                expected_compute_node = copy.deepcopy(compute_node)
                cells_utils.add_cell_to_compute_node(expected_compute_node,
                                                     cell_name)
                expected_response.append(expected_compute_node)
            response = messaging.Response(cell_name, compute_nodes, False)
            responses.append(response)
        self.mox.StubOutWithMock(self.msg_runner,
                                 'compute_node_get_all')
        self.msg_runner.compute_node_get_all(self.ctxt,
                hypervisor_match='fake-match').AndReturn(responses)
        self.mox.ReplayAll()
        response = self.cells_manager.compute_node_get_all(self.ctxt,
                hypervisor_match='fake-match')
        self.assertEqual(expected_response, response)

    def test_compute_node_stats(self):
        raw_resp1 = {'key1': 1, 'key2': 2}
        raw_resp2 = {'key2': 1, 'key3': 2}
        raw_resp3 = {'key3': 1, 'key4': 2}
        responses = [messaging.Response('cell1', raw_resp1, False),
                     messaging.Response('cell2', raw_resp2, False),
                     messaging.Response('cell2', raw_resp3, False)]
        expected_resp = {'key1': 1, 'key2': 3, 'key3': 3, 'key4': 2}

        self.mox.StubOutWithMock(self.msg_runner,
                                 'compute_node_stats')
        self.msg_runner.compute_node_stats(self.ctxt).AndReturn(responses)
        self.mox.ReplayAll()
        response = self.cells_manager.compute_node_stats(self.ctxt)
        self.assertEqual(expected_resp, response)

    def test_compute_node_get(self):
        fake_cell = 'fake-cell'
        fake_response = messaging.Response(fake_cell,
                                           FAKE_COMPUTE_NODES[0],
                                           False)
        expected_response = copy.deepcopy(FAKE_COMPUTE_NODES[0])
        cells_utils.add_cell_to_compute_node(expected_response, fake_cell)
        cell_and_id = cells_utils.cell_with_item(fake_cell, 'fake-id')
        self.mox.StubOutWithMock(self.msg_runner,
                                 'compute_node_get')
        self.msg_runner.compute_node_get(self.ctxt,
                'fake-cell', 'fake-id').AndReturn(fake_response)
        self.mox.ReplayAll()
        response = self.cells_manager.compute_node_get(self.ctxt,
                compute_id=cell_and_id)
        self.assertEqual(expected_response, response)
