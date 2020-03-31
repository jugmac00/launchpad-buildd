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
from lpbuildd.target.snapbuildproxy import SnapBuildProxyOperationMixin
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildOCI(SnapBuildProxyOperationMixin, VCSOperationMixin,
               SnapStoreOperationMixin, Operation):

    description = "Build an OCI image."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildOCI, cls).add_arguments(parser)
        parser.add_argument(
            "--build-file", help="path to Dockerfile in branch")
        parser.add_argument("name", help="name of snap to build")

    def __init__(self, args, parser):
        super(BuildOCI, self).__init__(args, parser)
        self.bin = os.path.dirname(sys.argv[0])

    def _add_docker_engine_proxy_settings(self):
        """Add systemd file for docker proxy settings."""
        # Create containing directory for systemd overrides
        self.backend.run(
            ["mkdir", "-p", "/etc/systemd/system/docker.service.d"])
        # we need both http_proxy and https_proxy. The contents of the files
        # are otherwise identical
        for setting in ['http_proxy', 'https_proxy']:
            contents = dedent("""[Service]
                Environment="{}={}"
                """.format(setting.upper(), self.args.proxy_url))
            file_path = "/etc/systemd/system/docker.service.d/{}.conf".format(
                setting)
            with tempfile.NamedTemporaryFile(mode="w+") as systemd_file:
                systemd_file.write(contents)
                systemd_file.flush()
                self.backend.copy_in(systemd_file.name, file_path)

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
        deps = []
        if self.args.proxy_url:
            deps.extend(self.proxy_deps)
            self.install_git_proxy()
            # Add any proxy settings that are needed
            self._add_docker_engine_proxy_settings()
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
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        self.vcs_fetch(self.args.name, cwd="/home/buildd", env=env)

    def build(self):
        logger.info("Running build phase...")
        buildd_path = os.path.join("/home/buildd", self.args.name)
        args = ["docker", "build", "--no-cache"]
        if self.args.proxy_url:
            for var in ("http_proxy", "https_proxy"):
                args.extend(
                    ["--build-arg", "{}={}".format(var, self.args.proxy_url)])
        args.extend(["--tag", self.args.name])
        if self.args.build_file is not None:
            build_file_path = os.path.join(buildd_path, self.args.build_file)
            args.extend(["--file", build_file_path])
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
