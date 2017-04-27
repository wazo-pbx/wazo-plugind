# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import os
import uuid
from hamcrest import assert_that, calling, equal_to, has_entries, has_property
from requests import HTTPError
from wazo_plugind_client import Client
from xivo_test_helpers.hamcrest.raises import raises
from .test_api import BaseIntegrationTest

VALID_TOKEN = 'valid-token'


class UUIDMatcher(object):

    def __eq__(self, other):
        try:
            uuid.UUID(other)
            return True
        except:
            return False

    def __ne__(self, other):
        return not self == other


ANY_UUID = UUIDMatcher()


class TestPluginInstallation(BaseIntegrationTest):

    asset = 'plugind_only'

    def test_when_it_works(self):
        self.remove_from_asset('/tmp/build_success')
        self.remove_from_asset('/tmp/install_success')
        self.remove_from_asset('/tmp/install_failed')

        result = self.install_plugin(url='/tmp/repo', method='git')

        build_success_exists = self.exists_in_asset('tmp/build_success')
        install_success_exists = self.exists_in_asset('tmp/install_success')
        install_failed_exists = self.exists_in_asset('tmp/install_failed')

        assert_that(result, has_entries(uuid=ANY_UUID))
        assert_that(build_success_exists, 'build_success was not created or copied')
        assert_that(install_success_exists, 'install_success was not created')
        assert_that(install_failed_exists, equal_to(False), 'install_failed should not exists')

    def test_that_an_unauthorized_token_return_401(self):
        assert_that(calling(self.install_plugin).with_args(url='/tmp/repo', method='git', token='expired'),
                    raises(HTTPError).matching(has_property('response', has_property('status_code', 401))))

    def install_plugin(self, url, method, token=VALID_TOKEN):
        port = self.service_port(9503)
        client = Client('localhost', port=port, token=token, verify_certificate=False)
        return client.plugins.install(url, method)

    def exists_in_asset(self, path):
        complete_path = self.path_in_asset(path)
        return os.path.exists(complete_path)

    def remove_from_asset(self, path):
        complete_path = self.path_in_asset(path)
        try:
            os.unlink(complete_path)
        except OSError:
            return

    def path_in_asset(self, path):
        return os.path.join(self.assets_root, self.asset, path)
