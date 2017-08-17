# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
from . import db
from .exceptions import PluginNotFoundException
from .helpers import exec_and_log, WazoVersionFinder
from .context import Context
from .tasks import PackageAndInstallTask, UninstallTask

logger = logging.getLogger(__name__)


class PluginService(object):

    def __init__(self, config, status_publisher, root_worker, executor, plugin_db, wazo_version_finder):
        self._build_dir = config['build_dir']
        self._deb_file = '{}.deb'.format(self._build_dir)
        self._config = config
        self._status_publisher = status_publisher
        self._plugin_db = plugin_db
        self._root_worker = root_worker
        self._executor = executor
        self._wazo_version_finder = wazo_version_finder

    def _exec(self, ctx, *args, **kwargs):
        log_debug = ctx.get_logger(logger.debug)
        log_error = ctx.get_logger(logger.error)
        exec_and_log(log_debug, log_error, *args, **kwargs)

    def count(self):
        return self._plugin_db.count()

    def count_from_market(self, market_proxy, *args, **kwargs):
        market_db = db.MarketDB(market_proxy, self._plugin_db)
        return market_db.count(*args, **kwargs)

    def create(self, method, **kwargs):
        task = PackageAndInstallTask(self._config, self._root_worker)
        wazo_version = self._wazo_version_finder.get_version()
        ctx = Context(self._config, method=method, install_args=kwargs, wazo_version=wazo_version)
        ctx.log(logger.info, 'installing %s...', kwargs)
        self._executor.submit(task.execute, ctx)
        return ctx.uuid

    def get_plugin_metadata(self, namespace, name):
        plugin = self._plugin_db.get_plugin(namespace, name)
        if not plugin.is_installed():
            raise PluginNotFoundException(namespace, name)
        return plugin.metadata()

    def new_market_proxy(self):
        return db.MarketProxy(self._config['market'])

    def list_(self):
        return self._plugin_db.list_()

    def list_from_market(self, market_proxy, *args, **kwargs):
        market_db = db.MarketDB(market_proxy, self._plugin_db)
        return market_db.list_(*args, **kwargs)

    def delete(self, namespace, name):
        ctx = Context(self._config, namespace=namespace, name=name)
        ctx.log(logger.info, 'uninstalling %s/%s...', namespace, name)
        plugin = self._plugin_db.get_plugin(namespace, name)
        if not plugin.is_installed():
            raise PluginNotFoundException(namespace, name)

        task = UninstallTask(self._config, self._root_worker)
        ctx = ctx.with_fields(package_name=plugin.debian_package_name)
        self._executor.submit(task.execute, ctx)
        return ctx.uuid

    @classmethod
    def from_config(cls, config, *args, **kwargs):
        kwargs['plugin_db'] = db.PluginDB(config)
        kwargs['wazo_version_finder'] = WazoVersionFinder(config)
        return cls(config, *args, **kwargs)
