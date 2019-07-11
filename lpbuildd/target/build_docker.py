# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import logging
import os.path
import sys

from lpbuildd.target.operation import Operation
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin
from lpbuildd.util import shell_escape


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildDocker(VCSOperationMixin, SnapStoreOperationMixin, Operation):

    description = "Build a Docker image."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildDocker, cls).add_arguments(parser)
        parser.add_argument("--proxy-url", help="builder proxy url")
        parser.add_argument(
            "--revocation-endpoint",
            help="builder proxy token revocation endpoint")
        parser.add_argument("--file", help="path to Dockerfile in branch")
        parser.add_argument("name", help="name of snap to build")

    def __init__(self, args, parser):
        super(BuildDocker, self).__init__(args, parser)
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
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.args.backend in ("lxd", "fake"):
            self.snap_store_set_proxy()
        self.backend.run(["snap", "install", "docker"])
        if self.args.proxy_url:
            self.backend.copy_in(
                os.path.join(self.bin, "snap-git-proxy"),
                "/usr/local/bin/snap-git-proxy")
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
        args.append(os.path.join("/home/buildd", self.args.name))
        self.run_build_command(args)

        # Make extraction directy
        self.backend.run(["mkdir", "-p", "/home/buildd/{}-extract".format(
            self.args.name)])

        # save the newly built image
        docker_save = "docker save {name} > /build/{name}.tar".format(
            name=shell_escape(self.args.name))
        save_args = ["/bin/bash", "-c", docker_save]
        self.run_build_command(save_args)

        # extract the saved image
        extract_args = [
            "tar", "-xf", "/build/{name}.tar".format(name=self.args.name),
            "-C", "/build/"
            ]
        self.run_build_command(extract_args)

        # Tar each layer separately
        build_dir_contents = self.backend.listdir('/build')
        for content in build_dir_contents:
            content_path = os.path.join('/build/', content)
            if not self.backend.isdir(content_path):
                continue
            tar_path = '/build/{}.tar.gz'.format(content)
            tar_args = ['tar', '-czvf', tar_path, content_path]
            self.run_build_command(tar_args)

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
