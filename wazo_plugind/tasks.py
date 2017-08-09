# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
import os
import shutil
import yaml
from threading import Thread
from .context import Context
from . import bus, debian, download, helpers
from .exceptions import PluginAlreadyInstalled, PluginValidationException
from .helpers import exec_and_log

logger = logging.getLogger(__name__)

_publisher = None


class UninstallTask(object):

    def __init__(self, config, root_worker):
        self._root_worker = root_worker
        self._remover = _PackageRemover(config, root_worker)
        self._publisher = get_publisher(config)
        self._debug_enabled = config['debug']

    def execute(self, ctx):
        return self._uninstall_and_publish(ctx)

    def _uninstall_and_publish(self, ctx):
        try:
            step = 'initializing'
            steps = [
                ('starting', lambda ctx: ctx),
                ('removing', self._remover.remove),
                ('completed', lambda ctx: ctx),
            ]
            for step, fn in steps:
                self._publisher.uninstall(ctx, step)
                ctx = fn(ctx)
        except Exception:
            ctx.log(logger.error, 'Unexpected error while %s', step, exc_info=self._debug_enabled)
            error_id = '{}_error'.format(step)
            message = '{} Error'.format(step.capitalize())
            self._publisher.uninstall_error(ctx, error_id, message)


class PackageAndInstallTask(object):

    def __init__(self, config, root_worker):
        self._root_worker = root_worker
        self._builder = _PackageBuilder(config, self._root_worker, _package_and_install_impl)

    def execute(self, ctx):
        return _package_and_install_impl(self._builder, ctx)


def _package_and_install_impl(builder, ctx):
    try:
        step = 'initializing'
        publisher = get_publisher(ctx.config)

        steps = [
            ('starting', lambda ctx: ctx),
            ('downloading', builder.download),
            ('extracting', builder.extract),
            ('validating', builder.validate),
            ('installing dependencies', builder.install_dependencies),
            ('building', builder.build),
            ('packaging', builder.package),
            ('updating', builder.update),
            ('installing', builder.install),
            ('cleaning', builder.clean),
            ('completed', lambda ctx: ctx),
        ]

        for step, fn in steps:
            publisher.install(ctx, step)
            ctx = fn(ctx)

    except PluginAlreadyInstalled:
        ctx.log(logger.info, '%s/%s is already installed', ctx.metadata['namespace'], ctx.metadata['name'])
        builder.clean(ctx)
        publisher.install(ctx, 'completed')
    except PluginValidationException as e:
        ctx.log(logger.info, 'Plugin validation exception %s', e.details)
        details = dict(e.details)
        details['install_args'] = dict(ctx.install_args)
        publisher.install_error(ctx, e.error_id, e.message, details=e.details)
    except Exception:
        debug_enabled = ctx.config['debug']
        ctx.log(logger.error, 'Unexpected error while %s', step, exc_info=debug_enabled)
        error_id = '{}_error'.format(step)
        message = '{} Error'.format(step.capitalize())
        details = {'install_args': dict(ctx.install_args)}
        publisher.install_error(ctx, error_id, message, details=details)
        builder.clean(ctx)


def get_publisher(config):
    global _publisher
    if not _publisher:
        logger.debug('Creating a new publisher...')
        _publisher = bus.StatusPublisher.from_config(config)
        publisher_thread = Thread(target=_publisher.run)
        publisher_thread.daemon = True
        publisher_thread.start()
    return _publisher


class _PackageRemover(object):

    def __init__(self, config, root_worker):
        self._config = config
        self._root_worker = root_worker

    def remove(self, ctx):
        result = self._root_worker.uninstall(ctx.uuid, ctx.package_name)
        if result is not True:
            raise Exception('Uninstallation failed')
        return ctx


class _PackageBuilder(object):

    def __init__(self, config, root_worker, package_install_fn):
        self._config = config
        self._downloader = download.Downloader(config)
        self._debian_file_generator = debian.Generator.from_config(config)
        self._root_worker = root_worker
        self._package_install_fn = package_install_fn

    def build(self, ctx):
        namespace, name = ctx.metadata['namespace'], ctx.metadata['name']
        installer_path = os.path.join(ctx.extract_path, self._config['default_install_filename'])
        ctx.log(logger.debug, 'building %s/%s', namespace, name)
        cmd = [installer_path, 'build']
        self._exec(ctx, cmd, cwd=ctx.extract_path)
        return ctx.with_fields(installer_path=installer_path, namespace=namespace, name=name)

    def clean(self, ctx):
        extract_path = getattr(ctx, 'extract_path', None)
        if not extract_path:
            return
        ctx.log(logger.debug, 'removing build directory %s', extract_path)
        shutil.rmtree(extract_path)
        return ctx

    def _debianize(self, ctx):
        ctx.log(logger.debug, 'debianizing %s/%s', ctx.namespace, ctx.name)
        ctx = self._debian_file_generator.generate(ctx)
        cmd = ['dpkg-deb', '--build', ctx.pkgdir]
        self._exec(ctx, cmd, cwd=ctx.extract_path)
        deb_path = os.path.join(ctx.extract_path, '{}.deb'.format(self._config['build_dir']))
        return ctx.with_fields(package_deb_file=deb_path)

    def download(self, ctx):
        return self._downloader.download(ctx)

    def extract(self, ctx):
        extract_path = os.path.join(self._config['extract_dir'], ctx.uuid)
        ctx.log(logger.debug, 'extracting to %s', extract_path)
        shutil.rmtree(extract_path, ignore_errors=True)
        shutil.move(ctx.download_path, extract_path)
        metadata_filename = os.path.join(extract_path, self._config['default_metadata_filename'])
        with open(metadata_filename, 'r') as f:
            metadata = yaml.safe_load(f)
        return ctx.with_fields(
            metadata=metadata,
            extract_path=extract_path,
        )

    def validate(self, ctx):
        validator = helpers.Validator.new_from_config(ctx.config)
        validator.validate(ctx.metadata)
        return ctx

    def install(self, ctx):
        result = self._root_worker.install(ctx.uuid, ctx.package_deb_file)
        if result is not True:
            raise Exception('Installation failed')
        return ctx

    def install_dependencies(self, ctx):
        dependencies = ctx.metadata.get('depends', [])
        for dependency in dependencies:
            ctx.log(logger.info, 'installing dependency %s', dependency)
            self.install_dependency(dependency)
        return ctx

    def install_dependency(self, dep):
        ctx = Context(self._config, method='market', install_args=dep)
        self._package_install_fn(self, ctx)

    def update(self, ctx):
        if not ctx.metadata.get('debian_depends'):
            return ctx

        result = self._root_worker.apt_get_update(ctx.uuid)
        if result is not True:
            raise Exception('apt-get update failed')
        return ctx

    def package(self, ctx):
        ctx.log(logger.debug, 'packaging %s/%s', ctx.namespace, ctx.name)
        pkgdir = os.path.join(ctx.extract_path, self._config['build_dir'])
        os.makedirs(pkgdir)
        cmd = ['fakeroot', ctx.installer_path, 'package']
        self._exec(ctx, cmd, cwd=ctx.extract_path, env=dict(os.environ, pkgdir=pkgdir))
        installed_plugin_data_path = os.path.join(
            pkgdir, 'usr/lib/wazo-plugind/plugins', ctx.namespace, ctx.name)
        os.makedirs(installed_plugin_data_path)
        plugin_data_path = os.path.join(ctx.extract_path, self._config['plugin_data_dir'])
        cmd = ['fakeroot', 'cp', '-R', plugin_data_path, installed_plugin_data_path]
        self._exec(ctx, cmd, cwd=ctx.extract_path)
        return self._debianize(ctx.with_fields(pkgdir=pkgdir))

    def _exec(self, ctx, *args, **kwargs):
        log_debug = ctx.get_logger(logger.debug)
        log_error = ctx.get_logger(logger.error)
        exec_and_log(log_debug, log_error, *args, **kwargs)
