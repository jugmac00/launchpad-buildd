# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test BuildManager directly.

Most tests are done on subclasses instead.
"""

import io
import re

from fixtures import TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import defer
from twisted.logger import FileLogObserver, formatEvent, globalLogPublisher

from lpbuildd.builder import Builder, BuildManager, _sanitizeURLs
from lpbuildd.tests.fakebuilder import FakeConfig


class TestSanitizeURLs(TestCase):
    """Unit-test URL sanitization.

    `lpbuildd.tests.test_buildd.LaunchpadBuilddTests` also covers some of
    this, but at a higher level.
    """

    def test_non_urls(self):
        lines = [b"not a URL", b"still not a URL"]
        self.assertEqual(lines, list(_sanitizeURLs(lines)))

    def test_url_without_credentials(self):
        lines = [b"Get:1 http://ftpmaster.internal focal InRelease"]
        self.assertEqual(lines, list(_sanitizeURLs(lines)))

    def test_url_with_credentials(self):
        lines = [
            b"Get:1 http://buildd:secret@ftpmaster.internal focal InRelease",
        ]
        expected_lines = [b"Get:1 http://ftpmaster.internal focal InRelease"]
        self.assertEqual(expected_lines, list(_sanitizeURLs(lines)))

    def test_multiple_urls(self):
        lines = [
            b"http_proxy=http://squid.internal:3128/ "
            b"GOPROXY=http://user:password@example.com/goproxy",
        ]
        expected_lines = [
            b"http_proxy=http://squid.internal:3128/ "
            b"GOPROXY=http://example.com/goproxy",
        ]
        self.assertEqual(expected_lines, list(_sanitizeURLs(lines)))

    def test_proxyauth(self):
        lines = [
            b"socat STDIO PROXY:builder-proxy.launchpad.dev:github.com:443,"
            b"proxyport=3128,proxyauth=user:blah",
        ]
        expected_lines = [
            b"socat STDIO PROXY:builder-proxy.launchpad.dev:github.com:443,"
            b"proxyport=3128",
        ]
        self.assertEqual(expected_lines, list(_sanitizeURLs(lines)))


class TestBuildManager(TestCase):
    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def setUp(self):
        super().setUp()
        self.log_file = io.StringIO()
        observer = FileLogObserver(
            self.log_file, lambda event: formatEvent(event) + "\n"
        )
        globalLogPublisher.addObserver(observer)
        self.addCleanup(globalLogPublisher.removeObserver, observer)

    @defer.inlineCallbacks
    def test_runSubProcess(self):
        config = FakeConfig()
        config.set("builder", "filecache", self.useFixture(TempDir()).path)
        builder = Builder(config)
        builder._log = io.BytesIO()
        manager = BuildManager(builder, "123")
        d = defer.Deferred()
        manager.iterate = d.callback
        manager.runSubProcess("echo", ["echo", "hello world"])
        code = yield d
        self.assertEqual(0, code)
        self.assertEqual(
            b"RUN: echo 'hello world'\n" b"hello world\n",
            builder._log.getvalue(),
        )
        self.assertEqual(
            "Build log: RUN: echo 'hello world'\n" "Build log: hello world\n",
            self.log_file.getvalue(),
        )

    @defer.inlineCallbacks
    def test_runSubProcess_bytes(self):
        config = FakeConfig()
        config.set("builder", "filecache", self.useFixture(TempDir()).path)
        builder = Builder(config)
        builder._log = io.BytesIO()
        manager = BuildManager(builder, "123")
        d = defer.Deferred()
        manager.iterate = d.callback
        manager.runSubProcess("echo", ["echo", "\N{SNOWMAN}".encode()])
        code = yield d
        self.assertEqual(0, code)
        self.assertEqual(
            "RUN: echo '\N{SNOWMAN}'\n" "\N{SNOWMAN}\n".encode(),
            builder._log.getvalue(),
        )
        self.assertEqual(
            ["Build log: RUN: echo '\N{SNOWMAN}'", "Build log: \N{SNOWMAN}"],
            [
                re.sub(r".*? \[-\] (.*)", r"\1", line)
                for line in self.log_file.getvalue().splitlines()
            ],
        )
