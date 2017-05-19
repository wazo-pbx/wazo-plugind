# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
import os
import re
import shutil
import yaml
import time
from threading import Thread
from .celery import worker
from . import bus, debian, download
from .exceptions import InvalidNamespaceException, InvalidNameException
from .helpers import exec_and_log

logger = logging.getLogger(__name__)

_publisher = None


@worker.app.task
def uninstall_and_publish(ctx, package_name):
    from .root_tasks import uninstall
    publisher = get_publisher(ctx.config)
    publisher.uninstall(ctx, 'removing')
    result = uninstall.apply_async(args=(ctx.uuid, package_name))
    while not result.ready():
        time.sleep(0.1)
    publisher.uninstall(ctx, 'completed')


@worker.app.task
def package_and_install(ctx):
    builder = _PackageBuilder(ctx.config)
    publisher = get_publisher(ctx.config)
    publisher.install(ctx, 'starting')
    publisher.install(ctx, 'downloading')
    ctx = builder.download(ctx)
    publisher.install(ctx, 'extracting')
    ctx = builder.extract(ctx)
    publisher.install(ctx, 'building')
    ctx = builder.build(ctx)
    publisher.install(ctx, 'packaging')
    ctx = builder.package(ctx)
    ctx = builder.debianize(ctx)
    publisher.install(ctx, 'installing')
    ctx = builder.install(ctx)
    publisher.install(ctx, 'completed')


def get_publisher(config):
    global _publisher
    if not _publisher:
        logger.debug('Creating a new publisher...')
        _publisher = bus.StatusPublisher.from_config(config)
        publisher_thread = Thread(target=_publisher.run)
        publisher_thread.daemon = True
        publisher_thread.start()
    return _publisher


class _PackageBuilder(object):

    valid_namespace = re.compile(r'^[a-z0-9]+$')
    valid_name = re.compile(r'^[a-z0-9-]+$')

    def __init__(self, config):
        self._config = config
        self._downloader = download.Downloader(config)
        self._debian_file_generator = debian.Generator.from_config(config)

    def build(self, ctx):
        namespace, name = ctx.metadata['namespace'], ctx.metadata['name']
        if self.valid_namespace.match(namespace) is None:
            raise InvalidNamespaceException()
        if self.valid_name.match(name) is None:
            raise InvalidNameException()
        installer_path = os.path.join(ctx.extract_path, self._config['default_install_filename'])
        ctx.log(logger.debug, 'building %s/%s', namespace, name)
        cmd = [installer_path, 'build']
        self._exec(ctx, cmd, cwd=ctx.extract_path)
        return ctx.with_fields(installer_path=installer_path, namespace=namespace, name=name)

    def debianize(self, ctx):
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
        ctx.log(logger.debug, 'extracting %s to %s', ctx.url, extract_path)
        shutil.rmtree(extract_path, ignore_errors=True)
        shutil.move(ctx.download_path, extract_path)
        metadata_filename = os.path.join(extract_path, self._config['default_metadata_filename'])
        with open(metadata_filename, 'r') as f:
            metadata = yaml.safe_load(f)
        return ctx.with_fields(
            metadata=metadata,
            extract_path=extract_path,
        )

    def install(self, ctx):
        from .root_tasks import install
        result = install.apply_async(args=(ctx.uuid, ctx.package_deb_file))
        while not result.ready():
            time.sleep(0.1)
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
        return ctx.with_fields(pkgdir=pkgdir)

    def _exec(self, ctx, *args, **kwargs):
        log_debug = ctx.get_logger(logger.debug)
        log_error = ctx.get_logger(logger.error)
        exec_and_log(log_debug, log_error, *args, **kwargs)
