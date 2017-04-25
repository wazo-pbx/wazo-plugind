# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import logging
from flask import make_response
from flask_restful import Resource
from pkg_resources import resource_string
from xivo.auth_verifier import AuthVerifier, required_acl
from xivo.rest_api_helpers import handle_api_exception

logger = logging.getLogger(__name__)


auth_verifier = AuthVerifier()


class _BaseResource(Resource):

    method_decorators = [auth_verifier.verify_token, handle_api_exception] + Resource.method_decorators

    @classmethod
    def add_resource(cls, api, *args, **kwargs):
        api.add_resource(cls, cls.api_path)


class Config(_BaseResource):

    api_path = '/config'
    _config = {}

    def get(self):
        # TODO: add an acl
        return {k: v for k, v in self._config.items()}, 200

    @classmethod
    def add_resource(cls, api, config):
        cls._config = config
        super().add_resource(api)


class Plugins(_BaseResource):

    api_path = '/plugins'

    @required_acl('plugind.plugins.create')
    def post(self):
        return {'hello': 'world'}


class Api(_BaseResource):

    api_package = 'wazo_plugind.swagger'
    api_filename = 'api.yml'
    api_path = '/api/api.yml'

    def get(self):
        try:
            api_spec = resource_string(self.api_package, self.api_filename)
        except IOError:
            return {'error': "API spec does not exist"}, 404

        return make_response(api_spec, 200, {'Content-Type': 'application/x-yaml'})
