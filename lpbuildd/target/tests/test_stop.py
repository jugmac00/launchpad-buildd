# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from textwrap import dedent

from fixtures import FakeLogger
from testtools import TestCase
from testtools.matchers import StartsWith

from lpbuildd.target.backend import BackendException
from lpbuildd.target.stop import Stop


class TestStop(TestCase):

    def test_succeeds(self):
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        stop = Stop(args=args)
        self.assertEqual(0, stop.run())
        self.assertEqual([((), {})], stop.backend.stop.calls)

    def test_fails(self):
        logger = self.useFixture(FakeLogger())
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        stop = Stop(args=args)
        stop.backend.stop.failure = BackendException
        self.assertEqual(1, stop.run())
        self.assertEqual([((), {})], stop.backend.stop.calls)
        self.assertThat(logger.output, StartsWith(dedent("""\
            Stopping target for build 1
            Failed to stop target
            Traceback (most recent call last):
            """)))
