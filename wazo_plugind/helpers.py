# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
import os
import re
import subprocess
from xivo.token_renewer import TokenRenewer
from xivo_auth_client import Client as AuthClient
from xivo_confd_client import Client as ConfdClient
from . import db
from .exceptions import (
    PluginAlreadyInstalled,
    PluginValidationException,
)
from .schema import new_plugin_metadata_schema

_DEFAULT_PLUGIN_FORMAT_VERSION = 0
logger = logging.getLogger(__name__)


def exec_and_log(stdout_logger, stderr_logger, *args, **kwargs):
    p = subprocess.Popen(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    out, err = p.communicate()
    cmd = ' '.join(args[0])
    if out:
        stdout_logger('%s\n==== STDOUT ====\n%s==== END ====', cmd, out.decode('utf8'))
    if err:
        stdout_logger('%s\n==== STDERR====\n%s==== END ====', cmd, err.decode('utf8'))
    return p


class Validator(object):

    valid_namespace = re.compile(r'^[a-z0-9]+$')
    valid_name = re.compile(r'^[a-z0-9-]+$')
    required_fields = ['name', 'namespace', 'version']

    def __init__(self, plugin_db, wazo_version_finder):
        self._db = plugin_db
        self._wazo_version_finder = wazo_version_finder

    def validate(self, metadata):
        current_version = self._wazo_version_finder.get_version()
        logger.debug('Using current version %s', current_version)
        logger.debug('max_wazo_version: %s', metadata.get('max_wazo_version', 'undefined'))

        body, errors = new_plugin_metadata_schema(current_version).load(metadata)
        if errors:
            raise PluginValidationException(errors)
        logger.debug('validated metadata: %s', body)

        if self._db.is_installed(metadata['namespace'], metadata['name'], metadata['version']):
            raise PluginAlreadyInstalled(metadata['namespace'], metadata['name'])

    @classmethod
    def new_from_config(cls, config):
        plugin_db = db.PluginDB(config)
        wazo_version_finder = _WazoVersionFinder(config)
        return cls(plugin_db, wazo_version_finder)


class _WazoVersionFinder(object):

    def __init__(self, config):
        self._token = None
        self._config = config
        self._token_renewer = TokenRenewer(AuthClient(**config['auth']))
        self._token_renewer.subscribe_to_token_change(self.set_token)

    def get_version(self):
        return os.getenv('WAZO_VERSION') or self._query_for_version()

    def set_token(self, token):
        self._token = token

    def _query_for_version(self):
        logger.debug('Using the current version from confd')
        with self._token_renewer:
            client = ConfdClient(token=self._token, **self._config['confd'])
            return client.infos()['wazo_version']
