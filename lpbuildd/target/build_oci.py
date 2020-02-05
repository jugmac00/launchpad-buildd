# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import logging
import os.path
import sys
import tempfile
from textwrap import dedent

from lpbuildd.target.operation import Operation
from lpbuildd.target.snapstore import (
    SnapStoreOperationMixin,
    SnapStoreProxyMixin,
)
from lpbuildd.target.vcs import VCSOperationMixin


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildOCI(SnapStoreProxyMixin, VCSOperationMixin,
               SnapStoreOperationMixin, Operation):

    description = "Build an OCI image."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildOCI, cls).add_arguments(parser)
        parser.add_argument("--file", help="path to Dockerfile in branch")
        parser.add_argument("name", help="name of snap to build")

    def __init__(self, args, parser):
        super(BuildOCI, self).__init__(args, parser)
        self.bin = os.path.dirname(sys.argv[0])

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

    def install(self):
        logger.info("Running install phase...")
        deps = super(BuildOCI, self).install()
        # Add any proxy settings that are needed
        if self.args.proxy_url:
            self.backend.run(
                ["mkdir", "-p", "/etc/systemd/system/docker.service.d"])
            with tempfile.NamedTemporaryFile(mode="w+") as http_file:
                http_contents = dedent("""[Service]
                Environment="HTTP_PROXY={}"
                """.format(self.args.proxy_url))
                http_file.write(http_contents)
                http_file.flush()
                self.backend.copy_in(
                    http_file.name,
                    "/etc/systemd/system/docker.service.d/http-proxy.conf")
            with tempfile.NamedTemporaryFile(mode="w+") as https_file:
                https_contents = dedent("""[Service]
                Environment="HTTPS_PROXY={}"
                """.format(self.args.proxy_url))
                https_file.write(https_contents)
                https_file.flush()
                self.backend.copy_in(
                    https_file.name,
                    "/etc/systemd/system/docker.service.d/https-proxy.conf")
        deps.extend(self.vcs_deps)
        deps.extend(["docker.io"])
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.args.backend in ("lxd", "fake"):
            self.snap_store_set_proxy()
        self.backend.run(["systemctl", "restart", "docker"])
        # The docker snap can't see /build, so we have to do our work under
        # /home/buildd instead.  Make sure it exists.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = OrderedDict()
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        self.vcs_fetch(self.args.name, cwd="/home/buildd", env=env)

    def build(self):
        logger.info("Running build phase...")
        args = ["docker", "build", "--no-cache"]
        if self.args.proxy_url:
            for var in ("http_proxy", "https_proxy"):
                args.extend(
                    ["--build-arg", "{}={}".format(var, self.args.proxy_url)])
        args.extend(["--tag", self.args.name])
        if self.args.file is not None:
            args.extend(["--file", self.args.file])
        buildd_path = os.path.join("/home/buildd", self.args.name)
        args.append(buildd_path)
        self.run_build_command(args)

    def run(self):
        try:
            self.install()
        except Exception:
            logger.exception('Install failed')
            return RETCODE_FAILURE_INSTALL
        try:
            self.repo()
            self.build()
        except Exception:
            logger.exception('Build failed')
            return RETCODE_FAILURE_BUILD
        return 0
