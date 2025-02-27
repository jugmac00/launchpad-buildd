# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import logging
import os.path
from textwrap import dedent

from lpbuildd.target.backend import check_path_escape
from lpbuildd.target.operation import Operation
from lpbuildd.target.proxy import BuilderProxyOperationMixin
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin

RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildOCI(
    BuilderProxyOperationMixin,
    VCSOperationMixin,
    SnapStoreOperationMixin,
    Operation,
):
    description = "Build an OCI image."

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--build-file", help="path to Dockerfile in branch"
        )
        parser.add_argument(
            "--build-path",
            default=".",
            help="context directory for docker build",
        )
        parser.add_argument(
            "--build-arg",
            default=[],
            action="append",
            help="A docker build ARG in the format of key=value. "
            "This option can be repeated many times. For example: "
            "--build-arg VAR1=A --build-arg VAR2=B",
        )
        parser.add_argument("name", help="name of image to build")

    def __init__(self, args, parser):
        super().__init__(args, parser)
        self.buildd_path = os.path.join("/home/buildd", self.args.name)

    def _add_docker_engine_proxy_settings(self):
        """Add systemd file for docker proxy settings."""
        # Create containing directory for systemd overrides
        self.backend.run(
            ["mkdir", "-p", "/etc/systemd/system/docker.service.d"]
        )
        # we need both http_proxy and https_proxy. The contents of the files
        # are otherwise identical
        for setting in ["http_proxy", "https_proxy"]:
            contents = dedent(
                f"""[Service]
                Environment="{setting.upper()}={self.args.proxy_url}"
                """
            )
            file_path = f"/etc/systemd/system/docker.service.d/{setting}.conf"
            with self.backend.open(file_path, mode="w+") as systemd_file:
                systemd_file.write(contents)

    def install(self):
        logger.info("Running install phase...")
        deps = []
        if self.args.proxy_url:
            deps.extend(self.proxy_deps)
            self.install_git_proxy()
            # Add any proxy settings that are needed
            self._add_docker_engine_proxy_settings()
        deps.extend(self.vcs_deps)
        self.backend.run(["apt-get", "-y", "install"] + deps)
        # XXX tushar5526 2025-02-14: Pin docker.io version to 20.x for OCI
        # builds after recent SRU update that bumped the version to 26.x.
        # This is placed temporarily until we add support for OCI compliant
        # layout added in 26.x
        # For more info: https://bugs.launchpad.net/launchpad/+bug/2098106
        self.backend.run(
            ["apt-get", "-y", "install", "docker.io=20.10.21-0ubuntu1~20.04.2"]
        )
        self.backend.run(["apt-mark", "hold", "docker.io"])
        if self.backend.supports_snapd:
            self.snap_store_set_proxy()
        self.backend.run(["systemctl", "restart", "docker"])
        # The docker snap can't see /build, so we have to do our work under
        # /home/buildd instead.  Make sure it exists.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        self.vcs_fetch(
            self.args.name,
            cwd="/home/buildd",
            env=env,
            git_shallow_clone_with_single_branch=True,
        )

    def build(self):
        logger.info("Running build phase...")
        args = ["docker", "build", "--no-cache"]
        if self.args.proxy_url:
            for var in ("http_proxy", "https_proxy"):
                args.extend(["--build-arg", f"{var}={self.args.proxy_url}"])
        args.extend(["--tag", self.args.name])
        if self.args.build_file is not None:
            build_file_path = os.path.join(
                self.args.build_path, self.args.build_file
            )
            check_path_escape(self.buildd_path, build_file_path)
            args.extend(["--file", build_file_path])

        # Keep this at the end, so we give the user a chance to override any
        # build-arg we set automatically (like http_proxy).
        for arg in self.args.build_arg:
            args.extend(["--build-arg=%s" % arg])

        build_context_path = os.path.join(
            self.buildd_path, self.args.build_path
        )
        check_path_escape(self.buildd_path, build_context_path)
        args.append(build_context_path)
        self.run_build_command(args)

    def run(self):
        try:
            self.install()
        except Exception:
            logger.exception("Install failed")
            return RETCODE_FAILURE_INSTALL
        try:
            self.repo()
            self.build()
        except Exception:
            logger.exception("Build failed")
            return RETCODE_FAILURE_BUILD
        return 0
