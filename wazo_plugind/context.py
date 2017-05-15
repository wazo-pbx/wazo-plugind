# Copyright 2017 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

from uuid import uuid4
from functools import partial


class Context(object):

    def __init__(self, config, **kwargs):
        self.uuid = str(uuid4())
        self.config = config
        self.with_fields(**kwargs)

    def log(self, logger, msg, *args):
        log_msg = '[{}] {}'.format(self.uuid, msg)
        logger(log_msg, *args)

    def get_logger(self, logger):
        return partial(self.log, logger)

    def with_fields(self, **kwargs):
        for field, value in kwargs.items():
            setattr(self, field, value)
        return self
