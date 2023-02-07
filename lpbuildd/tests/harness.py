# Copyright 2009-2011 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__all__ = [
    "BuilddTestCase",
]

try:
    from configparser import ConfigParser as SafeConfigParser
except ImportError:
    from ConfigParser import SafeConfigParser

import os
import shutil
import tempfile
import unittest
from textwrap import dedent

from fixtures import EnvironmentVariable, TempDir
from txfixtures.tachandler import TacTestFixture

from lpbuildd.builder import Builder


class MockBuildManager:
    """Mock BuildManager class.

    Only implements 'is_archive_private' and 'needs_sanitized_logs' as False.
    """

    is_archive_private = False

    @property
    def needs_sanitized_logs(self):
        return self.is_archive_private


class BuilddTestCase(unittest.TestCase):
    """Unit tests for logtail mechanisms."""

    def setUp(self):
        """Setup a Builder using the test config."""
        conf = SafeConfigParser()
        conf.add_section("builder")
        conf.set("builder", "architecturetag", "i386")
        conf.set("builder", "filecache", tempfile.mkdtemp())

        self.builder = Builder(conf)
        self.builder._log = True
        self.builder.manager = MockBuildManager()

        self.here = os.path.abspath(os.path.dirname(__file__))

    def tearDown(self):
        """Remove the 'filecache' directory used for the tests."""
        shutil.rmtree(self.builder._cachepath)

    def makeLog(self, size):
        """Inject data into the default buildlog file."""
        f = open(self.builder.cachePath("buildlog"), "w")
        f.write("x" * size)
        f.close()


class BuilddTestSetup(TacTestFixture):
    r"""Setup Builder for use by functional tests

    >>> fixture = BuilddTestSetup()
    >>> fixture.setUp()

    Make sure the server is running

    >>> try:
    ...     from xmlrpc.client import ServerProxy
    ... except ImportError:
    ...     from xmlrpclib import ServerProxy
    >>> s = ServerProxy('http://localhost:8321/rpc/')
    >>> s.echo('Hello World')
    ['Hello World']
    >>> fixture.tearDown()

    Again for luck !

    >>> fixture.setUp()
    >>> s = ServerProxy('http://localhost:8321/rpc/')

    >>> s.echo('Hello World')
    ['Hello World']

    >>> info = s.info()
    >>> len(info)
    3
    >>> print(info[:2])
    ['1.0', 'i386']

    >>> for buildtype in sorted(info[2]):
    ...     print(buildtype)
    binarypackage
    debian
    sourcepackagerecipe
    translation-templates

    >>> s.status()["builder_status"]
    'BuilderStatus.IDLE'

    >>> fixture.tearDown()
    """

    _root = None

    def setUp(self, **kwargs):
        # TacTestFixture defaults to /usr/bin/twistd, but on Ubuntu the
        # Python 3 version of this is /usr/bin/twistd3, so that makes for a
        # better default.
        if kwargs.get("twistd_script") is None:
            kwargs["twistd_script"] = "/usr/bin/twistd3"
        super().setUp(**kwargs)

    def setUpRoot(self):
        filecache = os.path.join(self.root, "filecache")
        os.mkdir(filecache)
        self.useFixture(EnvironmentVariable("HOME", self.root))
        test_conffile = os.path.join(self.root, "buildd.conf")
        with open(test_conffile, "w") as f:
            f.write(
                dedent(
                    f"""\
                [builder]
                architecturetag = i386
                filecache = {filecache}
                bindhost = localhost
                bindport = {self.daemon_port}
                sharepath = {self.root}
                """
                )
            )
        self.useFixture(EnvironmentVariable("BUILDD_CONFIG", test_conffile))
        # XXX cprov 2005-05-30:
        # When we are about running it seriously we need :
        # * install sbuild package
        # * to copy the scripts for sbuild

    @property
    def root(self):
        if self._root is None:
            self._root = self.useFixture(TempDir()).path
        return self._root

    @property
    def tacfile(self):
        return os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), os.path.pardir, "buildd.tac"
            )
        )

    @property
    def pidfile(self):
        return os.path.join(self.root, "buildd.pid")

    @property
    def logfile(self):
        return "/var/tmp/buildd.log"

    @property
    def daemon_port(self):
        return 8321
