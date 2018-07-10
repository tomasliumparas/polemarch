# pylint: disable=protected-access,no-member,unused-argument
from __future__ import unicode_literals

import os
import json
import logging
import collections
import uuid
import six
from docutils.core import publish_parts as rst_gen
from markdown2 import Markdown
from django.conf import settings
from django.db.models import Q
from django.core.validators import ValidationError
from vstutils.utils import ModelHandlers
from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

from . import hosts as hosts_models
from .vars import AbstractModel, AbstractVarsQuerySet, models
from ..exceptions import PMException
from ..utils import AnsibleModules
from .base import ManyToManyFieldACL, BQuerySet, BModel
from .hooks import Hook


logger = logging.getLogger("polemarch")


class ProjectQuerySet(AbstractVarsQuerySet):
    use_for_related_fields = True
    repo_handlers = ModelHandlers("REPO_BACKENDS", "'repo_type' variable needed!")
    task_handlers = ModelHandlers("TASKS_HANDLERS", "Unknown execution type!")


class Project(AbstractModel):
    PROJECTS_DIR = getattr(settings, "PROJECTS_DIR")
    objects       = ProjectQuerySet.as_manager()
    repo_handlers = objects._queryset_class.repo_handlers
    task_handlers = objects._queryset_class.task_handlers
    repository    = models.CharField(max_length=2*1024)
    status        = models.CharField(max_length=32, default="NEW")
    inventories   = ManyToManyFieldACL(hosts_models.Inventory, blank=True, null=True)
    hosts         = ManyToManyFieldACL(hosts_models.Host, blank=True, null=True)
    groups        = ManyToManyFieldACL(hosts_models.Group, blank=True, null=True)

    class Meta:
        default_related_name = "projects"

    class SyncError(Exception):
        pass

    class ReadMe(object):

        def __init__(self, project):
            self.path = project.path
            self.ext = None
            self.content = self.set_readme()

        def _make_ext(self, file_name):
            self.ext = os.path.splitext(file_name)[1]
            self.file_name = self.path + '/' + file_name

        def _make_rst(self, file):
            return rst_gen(file.read(), writer_name='html')['html_body']

        def _make_md(self, file):
            return Markdown().convert(file.read())

        def set_readme(self):
            if not os.path.exists(self.path):
                return
            for file in os.listdir(self.path):
                if file.lower() == 'readme.md' and self.ext is None:
                    self._make_ext(file)
                if file.lower() == 'readme.rst':
                    self._make_ext(file)
                    break
            if self.ext is not None:
                with open(self.file_name) as fd:
                    return getattr(self, '_make_{}'.format(str(self.ext)[1:]), str)(fd)

    HIDDEN_VARS = [
        'repo_password',
    ]

    BOOLEAN_VARS = [
        'repo_sync_on_run'
    ]

    EXTRA_OPTIONS = {
        'initiator': 0,
        'initiator_type': 'project',
        'executor': None,
        'save_result': True,
        'template_option': None
    }

    def __unicode__(self):
        return str(self.name)  # pragma: no cover

    def get_hook_data(self, when):
        data = super(Project, self).get_hook_data(when)
        data['type'] = self.type
        data['repository'] = self.repository
        return data

    @property
    def path(self):
        return "{}/{}".format(self.PROJECTS_DIR, self.id)

    @property
    def repo_class(self):
        repo_type = self.vars.get("repo_type", "MANUAL")
        return self.repo_handlers(repo_type, self)

    @property
    def type(self):
        try:
            return self.variables.get(key="repo_type").value
        except self.variables.model.DoesNotExist:
            return 'MANUAL'

    def check_path(self, inventory):
        if not isinstance(inventory, (six.string_types, six.text_type)):
            return
        path = "{}/{}".format(self.path, inventory)
        path = os.path.abspath(os.path.expanduser(path))
        if self.path not in path:
            raise ValidationError(dict(inventory="Inventory should be in project dir."))

    def _prepare_kw(self, kind, mod_name, inventory, **extra):
        self.check_path(inventory)
        if not mod_name:
            raise PMException("Empty playbook/module name.")
        history, extra = self.history.all().start(
            self, kind, mod_name, inventory, **extra
        )
        kwargs = dict(
            target=mod_name, inventory=inventory,
            history=history, project=self
        )
        kwargs.update(extra)
        return kwargs

    def hook(self, when, msg):
        Hook.objects.all().execute(when, msg)

    def sync_on_execution_handler(self, history):
        if not self.vars.get('repo_sync_on_run', False):
            return
        try:
            self.sync()
        except Exception as exc:
            raise self.SyncError("ERROR on Sync operation: " + str(exc))

    def execute(self, kind, *args, **extra):
        kind = kind.upper()
        task_class = self.task_handlers.backend(kind)
        sync = extra.pop("sync", False)

        kwargs = self._prepare_kw(kind, *args, **extra)
        history = kwargs['history']
        if sync:
            task_class(**kwargs)
        else:
            task_class.delay(**kwargs)
        return history.id if history is not None else history

    def set_status(self, status):
        self.status = status
        self.save()

    def start_repo_task(self, operation='sync'):
        self.set_status("WAIT_SYNC")
        return self.task_handlers.backend("REPO").delay(self, operation)

    def clone(self, *args, **kwargs):
        return self.repo_class.clone()

    def sync(self, *args, **kwargs):
        return self.repo_class.get()

    @property
    def revision(self):
        return self.repo_class.revision()

    @property
    def branch(self):
        return self.repo_class.get_branch_name()

    @property
    def module(self):
        return Module.objects.filter(Q(project=self)|Q(project=None))

    def __get_readme(self):
        readme = getattr(self, 'readme', None)
        if readme is None:
            self.readme = self.ReadMe(self)
            return self.readme
        return readme

    @property
    def readme_content(self):
        return self.__get_readme().content

    @property
    def readme_ext(self):
        return self.__get_readme().ext


class TaskFilterQuerySet(BQuerySet):
    use_for_related_fields = True


class Task(BModel):
    objects     = TaskFilterQuerySet.as_manager()
    project     = models.ForeignKey(Project, on_delete=models.CASCADE,
                                    related_query_name="playbook")
    name        = models.CharField(max_length=256, default=uuid.uuid1)
    playbook    = models.CharField(max_length=256)

    class Meta:
        default_related_name = "playbook"

    def __unicode__(self):
        return str(self.name)  # nocv


class ModulesQuerySet(BQuerySet):
    use_for_related_fields = True


class Module(BModel):
    objects = ModulesQuerySet.as_manager()
    path        = models.CharField(max_length=1024)
    _data       = models.CharField(max_length=4096, default='{}')
    project     = models.ForeignKey(Project,
                                    on_delete=models.CASCADE,
                                    related_query_name="modules",
                                    null=True, blank=True, default=None)

    class Meta:
        default_related_name = "modules"

    def __unicode__(self):
        return str(self.name)  # nocv

    @property
    def name(self):
        return self.data.get('module', None) or self.path.split('.')[-1]

    @property
    def data(self):
        return load(self._data, Loader=Loader)
