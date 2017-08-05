# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import subprocess
import time

from fixtures import FakeLogger
from systemfixtures import FakeTime
from testtools import TestCase
from testtools.matchers import (
    ContainsDict,
    Equals,
    MatchesDict,
    MatchesListwise,
    )

from lpbuildd.target.update import Update
from lpbuildd.tests.fakeslave import FakeMethod


class RanAptGet(MatchesListwise):

    def __init__(self, args_list):
        super(RanAptGet, self).__init__([
            MatchesListwise([
                Equals((["/usr/bin/apt-get"] + args,)),
                ContainsDict({
                    "env": MatchesDict({
                        "LANG": Equals("C"),
                        "DEBIAN_FRONTEND": Equals("noninteractive"),
                        "TTY": Equals("unknown"),
                        }),
                    }),
                ]) for args in args_list
            ])


class TestUpdate(TestCase):

    def test_succeeds(self):
        self.useFixture(FakeTime())
        start_time = time.time()
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        update = Update(args=args)
        self.assertEqual(0, update.run())

        expected_args = [
            ["-uy", "update"],
            ["-o", "DPkg::Options::=--force-confold", "-uy", "--purge",
             "dist-upgrade"],
            ]
        self.assertThat(update.backend.run.calls, RanAptGet(expected_args))
        self.assertEqual(start_time, time.time())

    def test_first_run_fails(self):
        class FailFirstTime(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailFirstTime, self).__call__(run_args, *args, **kwargs)
                if len(self.calls) == 1:
                    raise subprocess.CalledProcessError(1, run_args)

        logger = self.useFixture(FakeLogger())
        self.useFixture(FakeTime())
        start_time = time.time()
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        update = Update(args=args)
        update.backend.run = FailFirstTime()
        self.assertEqual(0, update.run())

        expected_args = [
            ["-uy", "update"],
            ["-uy", "update"],
            ["-o", "DPkg::Options::=--force-confold", "-uy", "--purge",
             "dist-upgrade"],
            ]
        self.assertThat(update.backend.run.calls, RanAptGet(expected_args))
        self.assertEqual(
            "Updating target for build 1\n"
            "Waiting 15 seconds and trying again ...\n",
            logger.output)
        self.assertEqual(start_time + 15, time.time())
