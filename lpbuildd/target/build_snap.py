# Copyright 2015-2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import json
import logging
import os.path
import sys
import tempfile
from textwrap import dedent
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

from lpbuildd.target.operation import Operation
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildSnap(VCSOperationMixin, SnapStoreOperationMixin, Operation):

    description = "Build a snap."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildSnap, cls).add_arguments(parser)
        parser.add_argument(
            "--channel-core", metavar="CHANNEL",
            help="install core snap from CHANNEL")
        parser.add_argument(
            "--channel-snapcraft", metavar="CHANNEL",
            help=(
                "install snapcraft as a snap from CHANNEL rather than as a "
                ".deb"))
        parser.add_argument(
            "--build-url", help="URL of this build on Launchpad")
        parser.add_argument("--proxy-url", help="builder proxy url")
        parser.add_argument(
            "--revocation-endpoint",
            help="builder proxy token revocation endpoint")
        parser.add_argument(
            "--build-source-tarball", default=False, action="store_true",
            help=(
                "build a tarball containing all source code, including "
                "external dependencies"))
        parser.add_argument("name", help="name of snap to build")

    def __init__(self, args, parser):
        super(BuildSnap, self).__init__(args, parser)
        self.slavebin = os.path.dirname(sys.argv[0])

    def run_build_command(self, args, env=None, **kwargs):
        """Run a build command in the target.

        :param args: the command and arguments to run.
        :param env: dictionary of additional environment variables to set.
        :param kwargs: any other keyword arguments to pass to Backend.run.
        """
        full_env = OrderedDict()
        full_env["LANG"] = "C.UTF-8"
        full_env["SHELL"] = "/bin/sh"
        if env:
            full_env.update(env)
        return self.backend.run(args, env=full_env, **kwargs)

    def save_status(self, status):
        """Save a dictionary of status information about this build.

        This will be picked up by the build manager and included in XML-RPC
        status responses.
        """
        status_path = os.path.join(self.backend.build_path, "status")
        with open("%s.tmp" % status_path, "w") as status_file:
            json.dump(status, status_file)
        os.rename("%s.tmp" % status_path, status_path)

    def install_svn_servers(self):
        proxy = urlparse(self.args.proxy_url)
        svn_servers = dedent("""\
            [global]
            http-proxy-host = {host}
            http-proxy-port = {port}
            """.format(host=proxy.hostname, port=proxy.port))
        # We should never end up with an authenticated proxy here since
        # lpbuildd.snap deals with it, but it's almost as easy to just
        # handle it as to assert that we don't need to.
        if proxy.username:
            svn_servers += "http-proxy-username = {}\n".format(proxy.username)
        if proxy.password:
            svn_servers += "http-proxy-password = {}\n".format(proxy.password)
        with tempfile.NamedTemporaryFile(mode="w+") as svn_servers_file:
            svn_servers_file.write(svn_servers)
            svn_servers_file.flush()
            os.fchmod(svn_servers_file.fileno(), 0o644)
            self.backend.run(["mkdir", "-p", "/root/.subversion"])
            self.backend.copy_in(
                svn_servers_file.name, "/root/.subversion/servers")

    def install(self):
        logger.info("Running install phase...")
        deps = []
        if self.args.backend == "lxd":
            # udev is installed explicitly to work around
            # https://bugs.launchpad.net/snapd/+bug/1731519.
            for dep in "snapd", "fuse", "squashfuse", "udev":
                if self.backend.is_package_available(dep):
                    deps.append(dep)
        deps.extend(self.vcs_deps)
        if self.args.proxy_url:
            deps.extend(["python3", "socat"])
        if self.args.channel_snapcraft:
            # snapcraft requires sudo in lots of places, but can't depend on
            # it when installed as a snap.
            deps.append("sudo")
        else:
            deps.append("snapcraft")
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.args.backend in ("lxd", "fake"):
            self.snap_store_set_proxy()
        if self.args.channel_core:
            self.backend.run(
                ["snap", "install",
                 "--channel=%s" % self.args.channel_core, "core"])
        if self.args.channel_snapcraft:
            self.backend.run(
                ["snap", "install", "--classic",
                 "--channel=%s" % self.args.channel_snapcraft, "snapcraft"])
        if self.args.proxy_url:
            self.backend.copy_in(
                os.path.join(self.slavebin, "snap-git-proxy"),
                "/usr/local/bin/snap-git-proxy")
            self.install_svn_servers()

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = OrderedDict()
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        self.vcs_fetch(self.args.name, cwd="/build", env=env)
        status = {}
        if self.args.branch is not None:
            status["revision_id"] = self.run_build_command(
                ["bzr", "revno"],
                cwd=os.path.join("/build", self.args.name),
                get_output=True).rstrip("\n")
        else:
            rev = (
                self.args.git_path
                if self.args.git_path is not None else "HEAD")
            status["revision_id"] = self.run_build_command(
                # The ^{} suffix copes with tags: we want to peel them
                # recursively until we get an actual commit.
                ["git", "rev-parse", rev + "^{}"],
                cwd=os.path.join("/build", self.args.name),
                get_output=True).rstrip("\n")
        self.save_status(status)

    @property
    def image_info(self):
        data = {}
        if self.args.build_url is not None:
            data["build_url"] = self.args.build_url
        return json.dumps(data)

    def pull(self):
        """Run pull phase."""
        logger.info("Running pull phase...")
        env = OrderedDict()
        env["SNAPCRAFT_LOCAL_SOURCES"] = "1"
        env["SNAPCRAFT_SETUP_CORE"] = "1"
        # XXX cjwatson 2017-11-24: Once we support building private snaps,
        # we'll need to make this optional in some way.
        env["SNAPCRAFT_BUILD_INFO"] = "1"
        env["SNAPCRAFT_IMAGE_INFO"] = self.image_info
        env["SNAPCRAFT_BUILD_ENVIRONMENT"] = "host"
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        self.run_build_command(
            ["snapcraft", "pull"],
            cwd=os.path.join("/build", self.args.name),
            env=env)
        if self.args.build_source_tarball:
            self.run_build_command(
                ["tar", "-czf", "%s.tar.gz" % self.args.name,
                 "--format=gnu", "--sort=name", "--exclude-vcs",
                 "--numeric-owner", "--owner=0", "--group=0",
                 self.args.name],
                cwd="/build")

    def build(self):
        """Run all build, stage and snap phases."""
        logger.info("Running build phase...")
        env = OrderedDict()
        # XXX cjwatson 2017-11-24: Once we support building private snaps,
        # we'll need to make this optional in some way.
        env["SNAPCRAFT_BUILD_INFO"] = "1"
        env["SNAPCRAFT_IMAGE_INFO"] = self.image_info
        env["SNAPCRAFT_BUILD_ENVIRONMENT"] = "host"
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        self.run_build_command(
            ["snapcraft"],
            cwd=os.path.join("/build", self.args.name),
            env=env)

    def run(self):
        try:
            self.install()
        except Exception:
            logger.exception('Install failed')
            return RETCODE_FAILURE_INSTALL
        try:
            self.repo()
            self.pull()
            self.build()
        except Exception:
            logger.exception('Build failed')
            return RETCODE_FAILURE_BUILD
        return 0
