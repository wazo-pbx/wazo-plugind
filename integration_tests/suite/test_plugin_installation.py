# -*- coding: utf-8 -*-
# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import os
from hamcrest import (
    assert_that,
    calling,
    contains,
    equal_to,
    empty,
    has_entry,
    has_entries,
    has_items,
    has_property,
    is_,
)
from requests import HTTPError
from mock import ANY
from xivo_test_helpers.hamcrest.raises import raises
from xivo_test_helpers.hamcrest.uuid_ import uuid_
from xivo_test_helpers import until
from .test_api import BaseIntegrationTest


class TestPluginList(BaseIntegrationTest):

    asset = 'plugind_only'

    def test_that_an_unauthorized_token_return_401(self):
        assert_that(calling(self.list_plugins).with_args(token='expired'),
                    raises(HTTPError).matching(has_property('response', has_property('status_code', 401))))

    def test_that_installed_plugins_are_listed(self):
        response = self.list_plugins()

        assert_that(response['total'], equal_to(0))
        assert_that(response['items'], empty())

        self.install_plugin(url='file:///data/git/repo', method='git', _async=False)

        result = self.list_plugins()

        assert_that(result['total'], equal_to(1))
        assert_that(result['items'], contains(has_entries(namespace='plugindtests',
                                                          name='foobar')))


class TestPluginInstallation(BaseIntegrationTest):

    asset = 'plugind_only'

    def test_when_it_works(self):
        self.uninstall_plugin(namespace='plugindtests', name='foobar', _async=False)

        result = self.install_plugin(url='file:///data/git/repo', method='git')

        assert_that(result, has_entries(uuid=uuid_()))

        statuses = ['starting', 'downloading', 'extracting', 'building',
                    'packaging', 'updating', 'installing', 'completed']
        for status in statuses:
            self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], status)

        build_success_exists = self.exists_in_container('/tmp/results/build_success')
        package_success_exists = self.exists_in_container('/tmp/results/package_success')
        install_success_exists = self.exists_in_container('/tmp/results/install_success')

        assert_that(build_success_exists, is_(True), 'build_success was not created or copied')
        assert_that(install_success_exists, is_(True), 'install_success was not created')
        assert_that(package_success_exists, is_(True), 'package_success was not created')

    def test_plugin_debian_dependency(self):
        dependency = 'tig'
        if self._is_installed(dependency):
            self.docker_exec(['apt-get' '-y', 'remove', dependency])

        self.install_plugin(url='file:///data/git/repo', method='git', _async=False)

        assert_that(self._is_installed(dependency), equal_to(True))

    def test_install_from_git_branch(self):
        msg_accumulator = self.new_message_accumulator('plugin.install.#')

        result = self.install_plugin(url='file:///data/git/repo', method='git', options=dict(ref='v2'))

        assert_that(result, has_entries(uuid=uuid_()))

        statuses = ['starting', 'downloading', 'extracting', 'building',
                    'packaging', 'updating', 'installing', 'completed']
        for status in statuses:
            self.assert_status_received(msg_accumulator, 'install', result['uuid'], status)

        package_success_exists = self.exists_in_container('/tmp/results/package_success_2')

        assert_that(package_success_exists, is_(True), 'package_success was not created')

    def test_with_a_postrm(self):
        self.install_plugin(url='file:///data/git/postrm', method='git', _async=False)

        self.uninstall_plugin(namespace='plugindtests', name='postrm', _async=False)

        postinst_success_exists = self.exists_in_container('/tmp/results/postinst_success')
        postrm_success_exists = self.exists_in_container('/tmp/results/postrm_success')

        assert_that(postinst_success_exists, equal_to(False))
        assert_that(postrm_success_exists, equal_to(True))

    def test_that_installing_twice_completes_with_reinstalling(self):
        self.install_plugin(url='file:///data/git/repo2', method='git', _async=False)

        result = self.install_plugin(url='file:///data/git/repo2', method='git')

        assert_that(result, has_entries(uuid=uuid_()))
        statuses = ['starting', 'downloading', 'extracting', 'validating', 'completed']
        for status in statuses:
            self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], status, exclusive=True)

    def test_when_uninstall_works(self):
        self.install_plugin(url='file:///data/git/repo', method='git', _async=False)

        result = self.uninstall_plugin(namespace='plugindtests', name='foobar')

        assert_that(result, has_entries(uuid=uuid_()))

        statuses = ['starting', 'removing', 'completed']
        for status in statuses:
            self.assert_status_received(self.msg_accumulator, 'uninstall', result['uuid'], status)

        build_success_exists = self.exists_in_container('/tmp/results/build_success')
        package_success_exists = self.exists_in_container('/tmp/results/package_success')

        assert_that(build_success_exists, is_(False), 'build_success was not removed')
        assert_that(package_success_exists, is_(False), 'package_success was not removed')

    def test_that_plugin_build_directory_is_removed_after_an_install(self):
        self.install_plugin(url='file:///data/git/repo', method='git', _async=False)

        directory_is_empty = self.directory_is_empty_in_container('/var/lib/wazo-plugind/tmp')

        assert_that(directory_is_empty, is_(True))

    def test_when_with_an_unknown_plugin_format_version(self):
        result = self.install_plugin(url='file:///data/git/futureversion', method='git')

        assert_that(result, has_entries(uuid=uuid_()))
        statuses = ['starting', 'error']
        for status in statuses:
            self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], status)

    def test_that_uninstalling_an_uninstalled_plugin_returns_404(self):
        assert_that(calling(self.uninstall_plugin).with_args(namespace='plugindtests',
                                                             name='uninstalled'),
                    raises(HTTPError).matching(has_property('response', has_property('status_code', 404))))

    def test_with_a_max_version_too_small(self):
        result = self.install_plugin(url='/data/git/max_version', method='git')

        errors = {
            u'error_id': u'validation_error',
            u'message': u'Validation error',
            u'resource': u'plugins',
            u'details': {
                u'max_wazo_version': {
                    u'message': ANY,
                    u'constraint': ANY,
                    u'constraint_id': u'range'}}}
        self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], 'error', errors=errors)

    def test_with_a_min_version_too_high(self):
        result = self.install_plugin(url='/data/git/min_version', method='git')

        errors = {
            u'error_id': u'validation_error',
            u'message': u'Validation error',
            u'resource': u'plugins',
            u'details': {
                u'min_wazo_version': {
                    u'message': ANY,
                    u'constraint': ANY,
                    u'constraint_id': u'range'}}}
        self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], 'error', errors=errors)

    def test_with_invalid_namespace(self):
        result = self.install_plugin(url='/data/git/fail_namespace', method='git')

        errors = {
            u'error_id': u'validation_error',
            u'message': u'Validation error',
            u'resource': u'plugins',
            u'details': {
                u'namespace': {
                    u'message': ANY,
                    u'constraint': u'^[a-z0-9]+$',
                    u'constraint_id': u'regex'}}}
        self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], 'error', errors=errors)

    def test_with_invalid_name(self):
        result = self.install_plugin(url='/data/git/fail_name', method='git')

        errors = {
            u'error_id': u'validation_error',
            u'message': u'Validation error',
            u'resource': u'plugins',
            u'details': {
                u'name': {
                    u'message': ANY,
                    u'constraint': u'^[a-z0-9-]+$',
                    u'constraint_id': u'regex'}}}
        self.assert_status_received(self.msg_accumulator, 'install', result['uuid'], 'error', errors=errors)

    def test_that_an_unauthorized_token_return_401(self):
        assert_that(calling(self.install_plugin).with_args(url='/data/git/repo', method='git', token='expired'),
                    raises(HTTPError).matching(has_property('response', has_property('status_code', 401))))

    def test_that_an_unauthorized_token_return_401_when_uninstall(self):
        assert_that(calling(self.uninstall_plugin).with_args(namespace='plugindtests',
                                                             name='foobar',
                                                             token='expired'),
                    raises(HTTPError).matching(has_property('response', has_property('status_code', 401))))

    def test_that_an_unknown_download_method_returns_400(self):
        assert_that(calling(self.install_plugin).with_args(url='/data/git/repo', method='svn'),
                    raises(HTTPError).matching(has_property('response', has_property('status_code', 400))))

    def test_that_an_out_of_date_debian_cache_does_not_break_package_install(self):
        self.install_plugin(url='file:///data/git/add_wazo_source_list', method='git', _async=False)
        self.install_plugin(url='file:///data/git/add_pubkeys', method='git', _async=False)

        ssh_key_installed = self.exists_in_container('/root/.ssh/authorized_keys2')

        assert_that(ssh_key_installed, equal_to(True))

    def list_file_in_container_dir(self, dir_path):
        output = self.docker_exec(['ls', dir_path])
        for current_filename in output.split('\n'):
            if not current_filename:
                continue
            yield current_filename

    def directory_is_empty_in_container(self, path):
        for filename in self.list_file_in_container_dir(path):
            return False
        return True

    def exists_in_container(self, path):
        directory, filename = os.path.split(path)
        for current_filename in self.list_file_in_container_dir(directory):
            if current_filename == filename:
                return True
        return False

    def _is_installed(self, search):
        installed_packages = self.docker_exec(['dpkg-query', '-W', '-f=${binary:Package}\n'])
        for debian_package in installed_packages.split('\n'):
            if debian_package == search:
                return True
        return False

    def assert_status_received(self, msg_accumulator, operation, uuid, status, exclusive=False, **kwargs):
        event_name = 'plugin_{}_progress'.format(operation)

        def match():
            expected_data = ['status', status, 'uuid', uuid]
            for key, value in kwargs.iteritems():
                expected_data.append(key)
                expected_data.append(value)

            received_msg = msg_accumulator.accumulate()
            assert_that(received_msg, has_items(
                has_entry('name', event_name),
                has_entry('data', has_entries(*expected_data))))

        def exclusive_match():
            while True:
                first = msg_accumulator.pop()

                # skip unrelated messages
                if first.get('data', {}).get('uuid') != uuid:
                    continue
                if first.get('name') != event_name:
                    continue

                if first['data']['status'] == status:
                    return

                msg_accumulator.push_back(first)
                self.fail('{} is not at the top of the accumulator, received {}'.format(status, first))

        aux = exclusive_match if exclusive else match
        until.assert_(aux, tries=120, interval=0.5,
                      message='The bus message should have been received: {}'.format(status))
