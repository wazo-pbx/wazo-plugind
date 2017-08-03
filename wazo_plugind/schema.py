# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

from marshmallow import pre_load, Schema, validates_schema, ValidationError
from xivo.mallow import fields
from xivo.mallow.validate import OneOf
from xivo.mallow.validate import Length
from xivo.mallow.validate import Range
from xivo.mallow.validate import Regexp
from .config import _MAX_PLUGIN_FORMAT_VERSION

_DEFAULT_PLUGIN_FORMAT_VERSION = 0


def new_plugin_metadata_schema(current_version):
    class PluginMetadataSchema(Schema):

        version_fields = ['version', 'max_wazo_version', 'min_wazo_version']

        name = fields.String(validate=Regexp(r'^[a-z0-9-]+$'), required=True)
        namespace = fields.String(validate=Regexp(r'^[a-z0-9]+$'), required=True)
        version = fields.String(required=True)
        plugin_format_version = fields.Integer(validate=Range(min=0,
                                                              max=_MAX_PLUGIN_FORMAT_VERSION),
                                               missing=_DEFAULT_PLUGIN_FORMAT_VERSION)
        max_wazo_version = fields.String(validate=Range(min=current_version))
        min_wazo_version = fields.String(validate=Range(max=current_version))
        depends = fields.Nested(MarketInstallOptionsSchema, many=True)

        @pre_load
        def ensure_string_versions(self, data):
            for field in self.version_fields:
                if field not in data:
                    continue
                value = data[field]
                if not isinstance(value, (float, int)):
                    continue
                data[field] = str(value)

    return PluginMetadataSchema()


class GitInstallOptionsSchema(Schema):

    ref = fields.String(missing='master', validate=Length(min=1))
    url = fields.String(validate=Length(min=1), required=True)


class MarketInstallOptionsSchema(Schema):

    namespace = fields.String(validate=Length(min=1), required=True)
    name = fields.String(validate=Length(min=1), required=True)
    version = fields.String()
    url = fields.String(validate=Length(min=1))


class MarketListRequestSchema(Schema):

    direction = fields.String(validate=OneOf(['asc', 'desc']), missing='asc')
    order = fields.String(validate=Length(min=1), missing='name')
    limit = fields.Integer(validate=Range(min=0), missing=None)
    offset = fields.Integer(validate=Range(min=0), missing=0)
    search = fields.String(missing=None)
    installed = fields.Boolean()

    @pre_load
    def ensure_dict(self, data):
        return data or {}


class OptionField(fields.Field):

    _options = {
        'git': fields.Nested(GitInstallOptionsSchema, missing=dict),
        'market': fields.Nested(MarketInstallOptionsSchema, required=True),
    }

    def _deserialize(self, value, attr, data):
        method = data.get('method')
        concrete_options = self._options.get(method)
        if not concrete_options:
            return {}
        return concrete_options._deserialize(value, attr, data)


class PluginInstallSchema(Schema):

    method = fields.String(validate=OneOf(['git', 'market']), required=True)
    options = OptionField(missing=dict, required=True)

    @pre_load
    def ensure_dict(self, data):
        return data or {}


# API 0.1 schema
class GitInstallOptionsSchemaV01(Schema):

    ref = fields.String(missing='master', validate=Length(min=1))


class MarketInstallOptionsSchemaV01(Schema):

    namespace = fields.String(validate=Length(min=1), required=True)
    name = fields.String(validate=Length(min=1), required=True)
    version = fields.String()


class OptionFieldV01(OptionField):

    _options = {
        'git': fields.Nested(GitInstallOptionsSchemaV01, missing=dict),
        'market': fields.Nested(MarketInstallOptionsSchemaV01, required=True),
    }


class PluginInstallSchemaV01(Schema):

    _method_optional_url = ['market']

    url = fields.String(validate=Length(min=1))
    method = fields.String(validate=OneOf(['git', 'market']), required=True)
    options = OptionFieldV01(missing=dict, required=True)

    @pre_load
    def ensure_dict(self, data):
        return data or {}

    @validates_schema
    def validate_url(self, data):
        method = data.get('method')
        if method in self._method_optional_url:
            return

        url = data.get('url')
        if not url:
            raise ValidationError([self.fields['url'].default_error_messages['required']], ['url'])
