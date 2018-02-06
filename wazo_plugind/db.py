# Copyright 2017-2018 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
import os
import re
import yaml
from unidecode import unidecode
from distutils.version import LooseVersion
from requests import HTTPError
from wazo_market_client import Client as MarketClient
from .exceptions import InvalidSortParamException, InvalidPackageNameException
from . import debian

logger = logging.getLogger(__name__)


class AlwaysLast(object):

    def __lt__(self, other):
        return False

    def __gt__(self, other):
        return True


LAST_ITEM = AlwaysLast()


def normalize_caseless(s):
    return unidecode(s).casefold()


def iin(left, right):
    """same as in for string but case incensitive"""
    if not isinstance(left, (str)) or not isinstance(right, (str)):
        try:
            return left in right
        except TypeError:
            return False

    return normalize_caseless(left) in normalize_caseless(right)


class MarketProxy(object):
    """The MarketProxy is an interface to the plugin market

    The proxy should be used during the execution of an HTTP request. It will fetch the content
    of the market and store it to allow multiple "queries" without having to do multiple HTTP
    requests on the "real" market.

    The proxy will only fetch the content of the market once, it is meant to be instanciated at
    each received HTTP request.
    """

    def __init__(self, market_config):
        self._client = MarketClient(**market_config)
        self._content = {}

    def get_content(self):
        if not self._content:
            self._content = self._fetch_plugin_list()
        return self._content

    def _fetch_plugin_list(self):
        try:
            result = self._client.plugins.list()
            return result['items']
        except HTTPError as e:
            logger.info('Failed to fetch plugins from the market %s', e.response.status_code)


class MarketPluginUpdater(object):

    def __init__(self, plugin_db, current_wazo_version):
        self._plugin_db = plugin_db
        self._current_wazo_version = current_wazo_version

    def update(self, plugin_info):
        namespace, name = plugin_info['namespace'], plugin_info['name']
        plugin = self._plugin_db.get_plugin(namespace, name)

        self._add_installed_version(plugin_info, plugin)
        self._add_upgradable_field(plugin_info, plugin)

        return plugin_info

    def _add_installed_version(self, plugin_info, plugin):
        installed_version = plugin.metadata()['version'] if plugin.is_installed() else None
        plugin_info['installed_version'] = installed_version

    def _add_upgradable_field(self, plugin_info, plugin):
        for version_info in plugin_info.get('versions', []):
            version_info['upgradable'] = True

            min_wazo_version = version_info.get('min_wazo_version', self._current_wazo_version)
            max_wazo_version = version_info.get('max_wazo_version', self._current_wazo_version)
            proposed_version = version_info.get('version')

            if _version_less_than(self._current_wazo_version, min_wazo_version):
                version_info['upgradable'] = False
            elif _version_less_than(max_wazo_version, self._current_wazo_version):
                version_info['upgradable'] = False
            elif plugin.is_installed():
                installed_version = plugin.metadata()['version']
                if not _version_less_than(installed_version, proposed_version):
                    version_info['upgradable'] = False


class MarketDB(object):

    def __init__(self, market_proxy, current_wazo_version, plugin_db=None):
        self._market_proxy = market_proxy
        self._updater = MarketPluginUpdater(plugin_db, current_wazo_version)

    def count(self, *args, **kwargs):
        content = self._market_proxy.get_content()
        if kwargs.get('filtered', False):
            filters = self._extract_strict_filters(**kwargs)
            content = self._strict_filter(content, **filters)
            content = list(self._filter(content, **kwargs))
        return len(content)

    def get(self, namespace, name):
        filters = dict(
            namespace=namespace,
            name=name,
        )

        content = self._market_proxy.get_content()
        content = self._add_local_values(content)
        content = self._strict_filter(content, **filters)

        if not content:
            raise LookupError('No such plugin {}'.format(filters))

        return content[0]

    def list_(self, *args, **kwargs):
        filters = self._extract_strict_filters(**kwargs)

        content = self._market_proxy.get_content()
        content = self._add_local_values(content)
        content = self._strict_filter(content, **filters)
        content = self._filter(content, **kwargs)
        content = self._sort(content, **kwargs)
        content = self._paginate(content, **kwargs)

        return content

    def _add_local_values(self, content):
        for metadata in content:
            self._updater.update(metadata)
        return content

    @staticmethod
    def _extract_strict_filters(filtered=None, search=None, limit=None, offset=None, order=None,
                                direction=None, installed=None, **kwargs):
        if installed is not None and 'installed_version' not in kwargs:
            kwargs['installed_version'] = InstalledVersionMatcher(installed)
        return kwargs

    @staticmethod
    def _filter(content, search=None, **kwargs):
        if not search:
            return content

        def f(item):
            for v in item.values():
                if iin(search, v):
                    return True
                if not isinstance(v, (list, tuple)):
                    continue
                for element in v:
                    if iin(search, element):
                        return True
            return False

        return filter(f, content)

    @staticmethod
    def _paginate(content, limit=None, offset=0, **kwargs):
        end = limit + offset if limit else None
        return content[offset:end]

    @staticmethod
    def _sort(content, order=None, direction=None, **kwargs):
        reverse = direction == 'desc'

        def key(element):
            return element.get(order, LAST_ITEM)

        try:
            return sorted(content, key=key, reverse=reverse)
        except TypeError:
            raise InvalidSortParamException(order)

    @staticmethod
    def _strict_filter(content, **kwargs):
        def match(metadata):
            for key, value in kwargs.items():
                if metadata.get(key) != value:
                    return False
            return True

        return [metadata for metadata in content if match(metadata)]


class PluginDB(object):

    def __init__(self, config):
        self._config = config
        self._debian_package_section = config['debian_package_section']
        self._debian_package_db = debian.PackageDB()

    def count(self):
        return len(self.list_())

    def get_plugin(self, namespace, name):
        return Plugin(self._config, namespace, name)

    def is_installed(self, namespace, name, version=None):
        return Plugin(self._config, namespace, name).is_installed(version)

    def list_(self):
        result = []
        debian_packages = self._debian_package_db.list_installed_packages(self._debian_package_section)
        for debian_package in debian_packages:
            try:
                plugin = Plugin.from_debian_package(self._config, debian_package)
                result.append(plugin.metadata())
            except (IOError, InvalidPackageNameException):
                logger.info('no metadata file found for %s/%s', plugin.namespace, plugin.name)
        return result


class Plugin(object):

    def __init__(self, config, namespace, name):
        self.namespace = namespace
        self.name = name
        self.debian_package_name = '{}-{}-{}'.format(config['default_debian_package_prefix'], name, namespace)
        self.metadata_filename = os.path.join(
            config['metadata_dir'],
            self.namespace,
            self.name,
            config['default_metadata_filename'],
        )
        self._metadata = None

    def is_installed(self, version=None):
        try:
            metadata = self.metadata()
        except IOError:
            return False

        if metadata is None:
            return False
        if version is None:
            return True

        installed_version = LooseVersion(metadata['version'])
        return self._cmp_version_string(installed_version, version.replace(' ', ''))

    def metadata(self):
        if not self._metadata:
            with open(self.metadata_filename, 'r') as f:
                self._metadata = yaml.load(f)

        return self._metadata

    def _cmp_version_string(self, installed_version, s):
        if not s:
            return True

        operator, end = self._extract_operator(s)
        version, end = self._extract_version(end)

        current = self._cmp_versions(operator, installed_version, version)
        return current and self._cmp_version_string(installed_version, end)

    @staticmethod
    def _cmp_versions(operator, left, right):
        if operator == '>':
            return left > right
        if operator == '>=':
            return left >= right
        elif operator == '<':
            return left < right
        elif operator == '<=':
            return left <= right
        else:
            return left == right

    @staticmethod
    def _extract_version(s):
        if ',' not in s:
            return s, None

        version, end = s.split(',', 1)
        return LooseVersion(version), end or None

    @staticmethod
    def _extract_operator(s):
        operator_chars = ['=', '>', '<']
        for i, c in enumerate(s):
            if c in operator_chars:
                continue
            operator = s[:i]
            end = s[i:]
            return operator, end

    @staticmethod
    def _extract_namespace_and_name(package_name_prefix, package_name):
        package_name_pattern = re.compile(r'^{}-([a-z0-9-]+)-([a-z0-9]+)$'.format(package_name_prefix))
        matches = package_name_pattern.match(package_name)
        if not matches:
            raise InvalidPackageNameException(package_name)
        return matches.group(2), matches.group(1)

    @classmethod
    def from_debian_package(cls, config, debian_package_name):
        package_name_prefix = config['default_debian_package_prefix']
        namespace, name = cls._extract_namespace_and_name(package_name_prefix, debian_package_name)
        return cls(config, namespace, name)


class InstalledVersionMatcher(object):

    def __init__(self, installed):
        self._installed = installed

    def __eq__(self, other):
        is_installed = other is not None
        return is_installed == self._installed

    def __ne__(self, other):
        return not self == other


def _make_comparable_version(version):
    try:
        value_tmp = LooseVersion(version)
        value_tmp.version  # raise AttributeError if value is None
        version = value_tmp
    except (TypeError, AttributeError):
        # Integer raise TypeError
        # Not a valid version number fallback to alphabetic ordering
        version = str(version)

    return version


def _version_less_than(left, right):
    if not left:
        return True
    if not right:
        return False

    left = _make_comparable_version(left)
    right = _make_comparable_version(right)

    return left < right
