import tempfile
import shutil
import uuid
import six
import git
from datetime import timedelta
from django.utils.timezone import now
from ._base import BaseTestCase, os
from ..tasks import ScheduledTask


test_playbook_content = '''
---
- hosts: all
  gather_facts: False
  tasks:
    - name: Some local task
      command: uname
'''


class Object(object):
    pass


class BaseExecutionsTestCase(BaseTestCase):
    def setUp(self):
        super(BaseExecutionsTestCase, self).setUp()
        self.path = self._settings('PROJECTS_DIR', '/tmp/unknown')

    def get_project_dir(self, id, **kwargs):
        return '{}/{}'.format(self.path, id)

    def sync_project(self, id, **kwargs):
        return self.make_bulk([
            self.get_mod_bulk('project', id, {}, 'sync'),
            self.get_bulk('project', {}, 'get', pk=id)
        ])[1]['data']

    def create_project_test(self, name=None, repo_type="MANUAL", **kwargs):
        name = name or str(uuid.uuid1())
        status_after_create = kwargs.pop('status_new', 'NEW')
        status_after_sync = kwargs.pop('status', 'OK')
        status_after_rm_sync = kwargs.pop('status_rm', status_after_sync)
        result = self.mass_create_bulk('project', [
            dict(
                name=name, repository=kwargs.pop('repository', repo_type),
                variables=dict(repo_type=repo_type, **kwargs)
            )
        ])
        project_data = result[0]['data']
        self.assertEqual(project_data['status'], status_after_create)
        self.assertEqual(project_data['name'], name)
        project_data = self.sync_project(project_data['id'])
        self.assertEqual(project_data['status'], status_after_sync)
        self.assertEqual(project_data['name'], name)
        self.remove_project_dir(project_data['id'])
        project_data = self.sync_project(project_data['id'])
        self.assertEqual(project_data['status'], status_after_rm_sync)
        self.assertEqual(project_data['name'], name)
        return project_data

    def remove_project_dir(self, id, **kwargs):
        shutil.rmtree(self.get_project_dir(id, **kwargs))

    def remove_project(self, id, **kwargs):
        self.get_result('delete', self.get_url('project', id))
        self.assertFalse(os.path.exists(self.get_project_dir(id, **kwargs)))

    def project_workflow(self, repo_type, **kwargs):
        execute = kwargs.pop('execute', False)
        project_data = self.create_project_test(str(uuid.uuid1()), repo_type, **kwargs)
        self.remove_project_dir(**project_data)
        self.remove_project(**project_data)
        project_data = self.create_project_test(str(uuid.uuid1()), repo_type, **kwargs)
        try:
            if not execute:
                return
            kwargs = getattr(self, 'wip_{}'.format(repo_type.lower()), str)(project_data)
            kwargs = kwargs if not isinstance(kwargs, six.string_types) else dict()
            self.playbook_tests(project_data, **kwargs)
            self.module_tests(project_data)
        finally:
            self.remove_project(**project_data)

    def get_file_path(self, name, path):
        return "{}/{}".format(path, name)

    def generate_playbook(self, path, name='test', count=1, data=test_playbook_content):
        files = []
        if isinstance(name, (list, tuple)):
            _files = name[:count or len(name)]
        else:
            _files = ['{}-{}.yml'.format(name, i) for i in range(count or 1)]
        for filename in _files:
            file_path = self.get_file_path(filename, path)
            with open(file_path, 'w') as playbook:
                playbook.write(data)
            files.append(filename)
        return files

    def project_bulk_sync_and_playbooks(self, id, **kwargs):
        return [
            self.get_mod_bulk('project', id, {}, 'sync', 'post'),
            self.get_mod_bulk('project', id, {}, 'playbook', 'get'),
        ]

    def playbook_tests(self, prj, playbook_count=1, execute=None, inventory="localhost"):
        _exec = dict(
            connection="local", limit="docker",
            playbook="<1[data][results][0][playbook]>", inventory=inventory
        )
        bulk_data = self.project_bulk_sync_and_playbooks(prj['id'])
        bulk_data += [
            self.get_mod_bulk('project', prj['id'], _exec, 'execute-playbook'),
        ] if execute else []
        results = self.make_bulk(bulk_data, 'put')
        self.assertEqual(results[0]['status'], 200)
        self.assertEqual(results[1]['status'], 200)
        self.assertEqual(results[1]['data']['count'], playbook_count)
        if not execute:
            return
        self.assertEqual(results[2]['status'], 201)

    def module_tests(self, prj):
        bulk_data = [
            self.get_mod_bulk(
                'project', prj['id'], {}, 'module', 'get', filters='limit=20'
            ),
            self.get_mod_bulk(
                'project', prj['id'], {}, 'module', 'get', filters='path=redis'
            ),
            self.get_mod_bulk(
                'project', prj['id'], {}, 'module/<1[data][results][0][id]>', 'get'
            ),
        ]
        results = self.make_bulk(bulk_data, 'put')
        for result in results:
            self.assertEqual(result['status'], 200)
        self.assertTrue(results[0]['data']['count'] > 1000)
        self.assertEqual(results[1]['data']['count'], 1)
        self.assertEqual(results[1]['data']['results'][0]['name'], 'redis')
        self.assertEqual(results[2]['data']['data']['module'], 'redis')

    def get_complex_bulk(self, item, op='add', **kwargs):
        return self.get_bulk(item, kwargs, op)


class ProjectTestCase(BaseExecutionsTestCase):

    def tearDown(self):
        super(ProjectTestCase, self).tearDown()
        repo_dir = getattr(self, 'repo_dir', None)
        if repo_dir:
            shutil.rmtree(repo_dir)

    def wip_manual(self, project_data):
        files = self.generate_playbook(self.get_project_dir(**project_data))
        self.make_test_templates(project_data)
        self.make_test_periodic_task(project_data)
        return dict(playbook_count=len(files), execute=True)

    def wip_git(self, project_data):
        self.assertEqual(project_data['revision'], self.revisions[-1])
        self.assertEqual(project_data['branch'], 'master')
        new_branch_var = dict(key='repo_branch', value='new_branch')
        self.make_bulk([
            self.get_mod_bulk('project', project_data['id'], new_branch_var)
        ])
        project_data = self.sync_project(project_data['id'])
        self.assertEqual(project_data['revision'], self.revisions[0])
        self.assertEqual(project_data['branch'], 'new_branch')
        new_branch_var['value'] = 'master'
        self.make_bulk([
            self.get_mod_bulk('project', project_data['id'], new_branch_var)
        ])
        repo_autosync_var = dict(key='repo_sync_on_run', value='True')
        self.make_bulk([
            self.get_mod_bulk('project', project_data['id'], repo_autosync_var)
        ])
        return dict(playbook_count=len(self.revisions), execute=True)

    def make_test_templates(self, project_data):
        pk = project_data['id']
        template_module = dict(
            kind="Module",
            name='Test module template',
            data=dict(
                module="ping",
                group="all",
                inventory='localhost',
                args="",
                vars=dict(
                    forks=8,
                ),
            ),
            options=dict(
                one=dict(module='shell', args='uname'),
                two=dict(vars=dict(forks=1))
            )
        )
        template_playbook = dict(
            kind="Task",
            name='Test playbook template',
            data=dict(
                playbook="test-0.yml",
                inventory='localhost',
                vars=dict(
                    forks=8,
                ),
            ),
            options=dict(
                tree=dict(vars=dict(limit='localhost')),
                four=dict(vars=dict(forks=1))
            )
        )
        template_playbook['options']['tree']['vars']['private-key'] = 'PATH'
        m_opts = dict(option='one')
        p_opts = dict(option='four')
        bulk_data = [
            self.get_mod_bulk('project', pk, template_module, 'template'),
            self.get_mod_bulk('project', pk, template_playbook, 'template'),
            self.get_mod_bulk('project', pk, {}, 'template/<0[data][id]>/execute'),
            self.get_mod_bulk('project', pk, {}, 'template/<1[data][id]>/execute'),
            self.get_mod_bulk('project', pk, m_opts, 'template/<0[data][id]>/execute'),
            self.get_mod_bulk('project', pk, p_opts, 'template/<1[data][id]>/execute'),
            self.get_mod_bulk('project', pk, {}, 'template/<1[data][id]>', 'get'),
        ]
        results = self.make_bulk(bulk_data)
        for result in results:
            if result['status'] == 200:
                self.assertEqual(
                    result['data']['options']['tree']['vars']['private-key'],
                    '[~~ENCRYPTED~~]'
                )
                continue
            self.assertEqual(result['status'], 201)

        tmplt_mod = results[0]['data']
        tmplt_play = results[1]['data']
        results = self.get_result(
            'get', self.get_url('project', project_data['id'], 'history') + '?limit=4'
        )
        self.assertEqual(results['results'][-1]['status'], 'OK')
        self.assertEqual(results['results'][-1]['kind'], 'MODULE')
        self.assertEqual(results['results'][-1]['initiator_type'], 'template')
        self.assertEqual(results['results'][-1]['mode'], 'ping')
        self.assertEqual(results['results'][-2]['status'], 'OK')
        self.assertEqual(results['results'][-2]['kind'], 'PLAYBOOK')
        self.assertEqual(results['results'][-2]['initiator_type'], 'template')
        self.assertEqual(results['results'][-2]['mode'], 'test-0.yml')
        self.assertEqual(results['results'][-3]['status'], 'OK')
        self.assertEqual(results['results'][-3]['kind'], 'MODULE')
        self.assertEqual(results['results'][-3]['initiator_type'], 'template')
        self.assertEqual(results['results'][-3]['options']['template_option'], 'one')
        self.assertEqual(results['results'][-4]['status'], 'OK')
        self.assertEqual(results['results'][-4]['kind'], 'PLAYBOOK')
        self.assertEqual(results['results'][-4]['initiator_type'], 'template')
        self.assertEqual(results['results'][-4]['options']['template_option'], 'four')

        # Templates in periodic tasks
        ptask_data = [
            dict(
                kind='TEMPLATE', template=tmplt_mod['id'], template_opt='one',
                schedule="10", type="INTERVAL"
            ),
            dict(
                kind='TEMPLATE', template=tmplt_play['id'], template_opt='four',
                schedule="10", type="INTERVAL"
            ),
        ]
        bulk_data = [
            self.get_mod_bulk('project', pk, data, 'periodic_task')
            for data in ptask_data
        ]
        results = self.make_bulk(bulk_data)
        for result in results:
            self.assertEqual(result['status'], 201)
            ScheduledTask.delay(result['data']['id'])
        results = self.get_result(
            'get', self.get_url('project', project_data['id'], 'history') + '?limit=2'
        )
        self.assertEqual(results['results'][-1]['status'], 'OK')
        self.assertEqual(results['results'][-1]['kind'], 'MODULE')
        self.assertEqual(results['results'][-1]['initiator_type'], 'scheduler')
        self.assertEqual(results['results'][-1]['mode'], 'shell')
        self.assertEqual(results['results'][-2]['status'], 'OK')
        self.assertEqual(results['results'][-2]['kind'], 'PLAYBOOK')
        self.assertEqual(results['results'][-2]['initiator_type'], 'scheduler')
        self.assertEqual(results['results'][-2]['mode'], 'test-0.yml')

        # Try to send cencel message
        result = self.get_result(
            'post', self.get_url(
                'project', project_data['id'],
                'history/{}/cancel'.format(results['results'][-2]['id'])
            ),
            code=200
        )
        self.assertEqual(
            result['detail'], "Task canceled: {}".format(results['results'][-2]['id'])
        )
        # Check Templates without inventory
        invalid_template = dict(template_playbook)
        del invalid_template['data']['inventory']
        invalid_type_template = dict(template_playbook)
        invalid_type_template['kind'] = 'UnknownKind'
        bulk_data = [
            self.get_mod_bulk('project', pk, invalid_template, 'template'),
            self.get_mod_bulk('project', pk, invalid_type_template, 'template'),
        ]
        results = self.make_bulk(bulk_data, 'put')
        self.assertEqual(results[0]['status'], 400)
        self.assertEqual(
            results[0]['data']['detail']['inventory'], ["Inventory have to set."]
        )
        # self.assertEqual(
        #     results[0]['data']['detail']['project'], ["Project have to set."]
        # )
        self.assertEqual(results[1]['status'], 400)

    def make_test_periodic_task(self, project_data):
        # Check periodic tasks
        # Check correct values
        ptasks_data = [
            dict(
                mode="test-1.yml", schedule="10", type="INTERVAL",
                project=project_data['id'],
                inventory='localhost', name="one"
            ),
            dict(
                mode="test-1.yml",
                schedule="* */2 1-15 * sun,fri",
                type="CRONTAB", project=project_data['id'],
                inventory='localhost', name="two"
            ),
            dict(
                mode="test-1.yml", schedule="", type="CRONTAB",
                project=project_data['id'],
                inventory='localhost', name="thre"
            ),
            dict(
                mode="test-1.yml", schedule="30 */4", type="CRONTAB",
                project=project_data['id'],
                inventory='localhost', name="four"
            ),
            dict(
                mode="ping", schedule="10", type="INTERVAL",
                project=project_data['id'],
                kind="MODULE", name="one", inventory='localhost'
            )
        ]
        bulk_data = [
            self.get_mod_bulk('project', project_data['id'], data, 'periodic_task')
            for data in ptasks_data
        ]
        bulk_data += [
            self.get_mod_bulk(
                'project', project_data['id'], dict(key='connection', value='local'),
                'periodic_task/<0[data][id]>/variables'
            ),
            self.get_mod_bulk(
                'project', project_data['id'], dict(key='forks', value='5'),
                'periodic_task/<0[data][id]>/variables'
            ),
        ]
        results = self.make_bulk(bulk_data)
        for result in results:
            self.assertEqual(result['status'], 201)
        # Check incorrect values
        incorrect_ptasks_data = [
            dict(
                mode="test-1.yml", schedule="30 */4 foo", type="CRONTAB",
                project=project_data['id'], inventory='localhost'
            ),
            dict(
                mode="test-1.yml", schedule="30 */4", type="crontab",
                project=project_data['id'], inventory='localhost',
                name="four"
            ),
        ]
        bulk_data = [
            self.get_mod_bulk('project', project_data['id'], data, 'periodic_task')
            for data in incorrect_ptasks_data
        ]
        bulk_data += [
            self.get_mod_bulk(
                'project', project_data['id'], dict(key='incorrect_var', value='blabla'),
                'periodic_task/{}/variables'.format(results[0]['data']['id'])
            ),
            self.get_mod_bulk(
                'project', project_data['id'], dict(key='forks', value='3423kldf'),
                'periodic_task/{}/variables'.format(results[4]['data']['id'])
            ),
        ]
        results = self.make_bulk(bulk_data, 'put')
        self.assertEqual(results[0]['status'], 400)
        self.assertIn(
            "Invalid weekday literal", results[0]['data']['detail']['schedule'][0]
        )
        self.assertEqual(results[1]['status'], 400)
        self.assertEqual(results[2]['status'], 400)
        self.assertIn("Incorrect argument", results[2]['data']["detail"]['playbook'][0])
        self.assertIn('incorrect_var', results[2]['data']["detail"]['argument'][0])
        self.assertEqual(results[3]['status'], 400)
        self.assertIn("Incorrect argument", results[3]['data']["detail"]['module'][0])
        self.assertIn('forks', results[3]['data']["detail"]['argument'][0])

        # Try to execute now
        data = dict(
            mode="test-0.yml", schedule="10", type="INTERVAL", name="one",
            project=project_data['id'], inventory='localhost'
        )
        results = self.make_bulk([
            self.get_mod_bulk('project', project_data['id'], data, 'periodic_task'),
            self.get_mod_bulk(
                'project', project_data['id'], data,
                'periodic_task/<0[data][id]>/execute',
                'post'
            ),
            self.get_mod_bulk(
                'project', project_data['id'], {}, 'history/<1[data][history_id]>', 'get'
            ),
        ], 'put')
        self.assertEqual(results[0]['status'], 201)
        self.assertEqual(results[1]['status'], 201)
        self.assertEqual(results[1]['data']['detail'], "Started at inventory localhost.")
        self.assertEqual(results[2]['status'], 200)
        self.assertEqual(results[2]['data']['status'], "OK")
        # Just exec
        ScheduledTask.delay(results[0]['data']['id'])
        # Except on execution
        with self.patch('polemarch.main.utils.CmdExecutor.execute') as _exec:
            def _exec_error(*args, **kwargs):
                raise Exception("Some error")

            _exec.side_effect = _exec_error
            ScheduledTask.delay(results[0]['data']['id'])
            self.assertEquals(_exec.call_count, 1)
            _exec.reset_mock()

        # No task
        with self.patch('polemarch.main.utils.CmdExecutor.execute') as _exec:
            ScheduledTask.delay(999)
            self.assertEquals(_exec.call_count, 0)

        results = self.get_result(
            'get', self.get_url('project', project_data['id'], 'history') + '?limit=2'
        )
        self.assertEqual(results['results'][-1]['status'], 'OK')
        self.assertEqual(results['results'][-2]['status'], 'ERROR')

    def test_project_manual(self):
        self.project_workflow('MANUAL', execute=True)

    def test_project_tar(self):
        with self.patch('polemarch.main.repo._base._ArchiveRepo._download') as download:
            download.side_effect = [self.tests_path + '/test_repo.tar.gz'] * 10
            self.project_workflow(
                'TAR', repository='http://localhost:8000/test_repo.tar.gz', execute=True
            )

    def test_project_git(self):
        # Prepare repo
        self.repo_dir = tempfile.mkdtemp()
        self.generate_playbook(self.repo_dir, ['main.yml'])
        repo = git.Repo.init(self.repo_dir)
        repo.index.add(["main.yml"])
        repo.index.commit("no message")
        first_revision = repo.head.object.hexsha
        repo.create_head('new_branch')
        self.generate_playbook(self.repo_dir, ['other.yml'])
        repo.index.add(["other.yml"])
        repo.index.commit("no message 2")
        second_revision = repo.head.object.hexsha

        # Test project
        self.revisions = [first_revision, second_revision]
        self.project_workflow(
            'GIT', repository=self.repo_dir, repo_password='', execute=True
        )
        self.project_workflow(
            'GIT', repository=self.repo_dir, repo_branch='new_branch', repo_key='key'
        )

    def test_complex(self):
        hostlocl_v = dict(ansible_user='centos', ansible_ssh_private_key_file='PATH')
        groups1_v = dict(ansible_user='ubuntu', ansible_ssh_pass='mypass')
        complex_inventory_v = dict(
            ansible_ssh_private_key_file="PATH", custom_var1='hello_world'
        )
        bulk_data = [
            # Create hosts
            self.get_complex_bulk('host', name='127.0.1.1'),
            self.get_complex_bulk('host', name='127.0.1.[3:4]', type="RANGE"),
            self.get_complex_bulk('host', name='127.0.1.[5:6]', type="RANGE"),
            self.get_complex_bulk('host', name='hostlocl'),
            # Create groups
            self.get_complex_bulk('group', name='hosts1'),
            self.get_complex_bulk('group', name='hosts2'),
            self.get_complex_bulk('group', name='groups1', children=True),
            self.get_complex_bulk('group', name='groups2', children=True),
            self.get_complex_bulk('group', name='groups3', children=True),
            # Create inventory
            self.get_complex_bulk('inventory', name='complex_inventory'),
        ]
        # Create manual project
        bulk_data += [
            self.get_complex_bulk('project', name="complex", repository='MANUAL')
        ]
        # Set vars
        bulk_data += [
            self.get_mod_bulk('host', "<3[data][id]>", dict(key=k, value=v))
            for k, v in hostlocl_v.items()
        ]
        bulk_data += [
            self.get_mod_bulk('group', "<6[data][id]>", dict(key=k, value=v))
            for k, v in groups1_v.items()
        ]
        bulk_data += [
            self.get_mod_bulk('inventory', "<9[data][id]>", dict(key=k, value=v))
            for k, v in complex_inventory_v.items()
        ]
        # Add children
        bulk_data += [
            # to hosts1
            self.get_mod_bulk(
                'group', "<4[data][id]>", dict(id="<0[data][id]>"), 'host',
            ),
            self.get_mod_bulk(
                'group', "<4[data][id]>", dict(id="<3[data][id]>"), 'host',
            ),
            # to hosts2
            self.get_mod_bulk(
                'group', "<5[data][id]>", dict(id="<1[data][id]>"), 'host',
            ),
            self.get_mod_bulk(
                'group', "<5[data][id]>", dict(id="<2[data][id]>"), 'host',
            ),
            # to groups1
            self.get_mod_bulk(
                'group', "<6[data][id]>", dict(id="<7[data][id]>"), 'group',
            ),
            self.get_mod_bulk(
                'group', "<6[data][id]>", dict(id="<8[data][id]>"), 'group',
            ),
            # to groups2
            self.get_mod_bulk(
                'group', "<7[data][id]>", dict(id="<8[data][id]>"), 'group',
            ),
            # to groups3
            self.get_mod_bulk(
                'group', "<8[data][id]>", dict(id="<4[data][id]>"), 'group',
            ),
            self.get_mod_bulk(
                'group', "<8[data][id]>", dict(id="<5[data][id]>"), 'group',
            ),
            # to inventory
            self.get_mod_bulk(
                'inventory', "<9[data][id]>", dict(id="<6[data][id]>"), 'group',
            ),
            self.get_mod_bulk(
                'inventory', "<9[data][id]>", dict(id="<0[data][id]>"), 'host',
            ),
            self.get_mod_bulk(
                'inventory', "<9[data][id]>", dict(id="<1[data][id]>"), 'host',
            ),
            self.get_mod_bulk(
                'inventory', "<9[data][id]>", dict(id="<3[data][id]>"), 'host',
            ),
            # to project
            self.get_mod_bulk(
                'project', "<10[data][id]>", dict(id="<9[data][id]>"), 'inventory'
            ),
        ]
        # Execute actions
        _exec = dict(
            connection="local", inventory="<9[data][id]>",
            module="ping", group="all", args="", forks=1
        )
        bulk_data += [
            self.get_mod_bulk(
                'project', "<10[data][id]>", _exec, 'sync',
            ),
            self.get_mod_bulk(
                'project', "<10[data][id]>", _exec, 'execute-module',
            ),
            self.get_bulk(
                'history', {}, 'get',
                pk="<{}[data][history_id]>".format(len(bulk_data)+1)
            ),
            self.get_mod_bulk(
                'history', "<{}[data][history_id]>".format(len(bulk_data)+1), {},
                'raw', 'get', filters='color=yes'
            ),
        ]
        # additionaly test hooks
        self.hook_model.objects.all().delete()
        hook_urls = ['localhost:64000', 'localhost:64001']
        recipients = ' | '.join(hook_urls)
        data = [
            dict(type='HTTP', recipients=recipients, when='on_execution'),
            dict(type='HTTP', recipients=recipients, when='after_execution'),
        ]
        self.generate_hooks(hook_urls)
        self.mass_create_bulk('hook', data)
        response = Object()
        response.status_code = 200
        response.reason = None
        response.text = "OK"
        ##
        with self.patch('requests.post') as mock:
            iterations = 2 * len(hook_urls)
            mock.side_effect = [response] * iterations
            results = self.make_bulk(bulk_data, 'put')
            self.assertEqual(mock.call_count, iterations)
            self.hook_model.objects.all().delete()
        for result in results[:-4]+results[-3:-2]:
            self.assertEqual(result['status'], 201 or 200, result)
        inventory_data = results[9]['data']
        self.assertEqual(inventory_data['name'], 'complex_inventory')
        # Check history
        history = results[-2]['data']
        self.assertEqual(history['revision'], "NO VCS")
        self.assertEqual(history['mode'], _exec['module'])
        self.assertEqual(history['kind'], 'MODULE')
        self.assertEqual(history['inventory'], results[9]['data']['id'])
        self.assertEqual(history['status'], "OK")
        etalon = self._get_string_from_file('exemplary_complex_inventory')
        etalon = etalon.replace('PATH', '[~~ENCRYPTED~~]')
        etalon = etalon.replace('mypass', '[~~ENCRYPTED~~]')
        self.assertEqual(
            list(map(str.strip, str(history['raw_inventory']).split("\n"))),
            list(map(str.strip, etalon.split("\n")))
        )
        # Check clear output
        bulk_data = [
            self.get_mod_bulk(
                'history', history['id'], {}, 'raw', 'get',
            ),
            self.get_mod_bulk(
                'history', history['id'], {}, 'clear', 'delete',
            ),
            self.get_mod_bulk(
                'project', history['project'], {},
                'history/{}/raw'.format(history['id']), 'get',
            ),
        ]
        new_results = self.make_bulk(bulk_data)
        self.assertEqual(new_results[0]['status'], 200)
        self.assertEqual(new_results[1]['status'], 204)
        self.assertEqual(new_results[2]['status'], 200)
        self.assertEqual(new_results[2]['data']['detail'], "Output trancated.\n")
        # Check all_hosts
        self.mass_create_bulk('host', [
            dict(name='complex{}'.format(i)) for i in range(3)
        ])
        bulk_data = [
            self.get_mod_bulk(
                'inventory', results[9]['data']['id'], {}, 'all_hosts', 'get',
            ),
            self.get_mod_bulk(
                'inventory', results[9]['data']['id'], {}, 'all_hosts', 'post',
            ),
            self.get_mod_bulk(
                'inventory', results[9]['data']['id'], {}, 'all_groups', 'get',
            ),
            self.get_mod_bulk(
                'inventory', results[9]['data']['id'], {}, 'all_groups', 'post',
            ),
        ]
        new_results = self.make_bulk(bulk_data, 'put')
        self.assertEqual(new_results[0]['status'], 200)
        self.assertEqual(new_results[0]['data']['count'], 4)
        self.assertEqual(new_results[1]['status'], 405)
        self.assertEqual(new_results[2]['status'], 200)
        self.assertEqual(new_results[2]['data']['count'], 5)
        self.assertEqual(new_results[3]['status'], 405)


    def test_history_facts(self):
        history_kwargs = dict(project=None, mode="setup",
                              kind="MODULE",
                              raw_inventory="inventory",
                              raw_stdout="text",
                              inventory=None,
                              status="OK",
                              start_time=now() - timedelta(hours=15),
                              stop_time=now() - timedelta(hours=14))
        history = self.get_model_class('History').objects.create(**history_kwargs)
        stdout = self._get_string_from_file("facts_stdout")
        history.raw_stdout = stdout
        history.save()
        url = self.get_url('history', history.id, 'facts')
        parsed = self.get_result("get", url)
        self.assertCount(parsed, 6)
        self.assertEquals(parsed['172.16.1.31']['status'], 'SUCCESS')
        self.assertEquals(parsed['test.vst.lan']['status'], 'SUCCESS')
        self.assertEquals(parsed['172.16.1.29']['status'], 'SUCCESS')
        self.assertEquals(parsed['172.16.1.32']['status'], 'FAILED!')
        self.assertEquals(parsed['172.16.1.30']['status'], 'UNREACHABLE!')
        self.assertEquals(parsed['172.16.1.31']['ansible_facts']
                          ['ansible_memfree_mb'], 736)
        self.assertCount(
            parsed['test.vst.lan']['ansible_facts']["ansible_devices"], 2
        )
        self.assertIn('No route to host',
                      parsed['172.16.1.30']['msg'])
        for status in ['RUN', 'DELAY']:
            history.status = status
            history.save()
            self.get_result("get", url, code=424)
        history.status = "OK"
        history.kind = "PLAYBOOK"
        history.save()
        self.get_result("get", url, code=404)
