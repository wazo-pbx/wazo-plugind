# Copyright 2018 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0-or-later

from hamcrest import (
    assert_that,
    equal_to,
)
from unittest import TestCase

from .. import version


class TestLessThan(TestCase):

    def test_less_than(self):
        assert_that(version.less_than('17.10', '17.10'), equal_to(False))
        assert_that(version.less_than('17.09', '17.10'), equal_to(True))
        assert_that(version.less_than(None, '17.10'), equal_to(True))
        assert_that(version.less_than('17.10', None), equal_to(False))
        assert_that(version.less_than('', None), equal_to(True))
        assert_that(version.less_than('1.0.0', '1.0.0-1'), equal_to(True))
        assert_that(version.less_than('1.0.1', '1.0.0-1'), equal_to(False))
        assert_that(version.less_than('1.0.0-2', '1.0.0-10'), equal_to(True))
