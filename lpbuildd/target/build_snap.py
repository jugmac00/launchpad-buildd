# Copyright 2015-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import base64
from collections import OrderedDict
import json
import logging
import os.path
import subprocess
import sys
try:
    from urllib.error import (
        HTTPError,
        URLError,
        )
    from urllib.request import (
        Request,
        urlopen,
        )
    from urllib.parse import urlparse
except ImportError:
    from urllib2 import (
        HTTPError,
        Request,
        URLError,
        urlopen,
        )
    from urlparse import urlparse

from lpbuildd.target.operation import Operation


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildSnap(Operation):

    description = "Build a snap."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildSnap, cls).add_arguments(parser)
        build_from_group = parser.add_mutually_exclusive_group(required=True)
        build_from_group.add_argument(
            "--branch", metavar="BRANCH", help="build from this Bazaar branch")
        build_from_group.add_argument(
            "--git-repository", metavar="REPOSITORY",
            help="build from this Git repository")
        parser.add_argument(
            "--git-path", metavar="REF-PATH",
            help="build from this ref path in REPOSITORY")
        parser.add_argument("--proxy-url", help="builder proxy url")
        parser.add_argument(
            "--revocation-endpoint",
            help="builder proxy token revocation endpoint")
        parser.add_argument("name", help="name of snap to build")

    def __init__(self, args, parser):
        super(BuildSnap, self).__init__(args, parser)
        if args.git_repository is None and args.git_path is not None:
            parser.error("--git-path requires --git-repository")
        self.slavebin = os.path.dirname(sys.argv[0])
        # Set to False for local testing if your target doesn't have an
        # appropriate certificate for your codehosting system.
        self.ssl_verify = True

    def run_build_command(self, args, cwd="/build", env=None, **kwargs):
        """Run a build command in the target.

        :param args: the command and arguments to run.
        :param cwd: run the command in this working directory in the target.
        :param env: dictionary of additional environment variables to set.
        :param kwargs: any other keyword arguments to pass to Backend.run.
        """
        full_env = OrderedDict()
        full_env["LANG"] = "C.UTF-8"
        full_env["SHELL"] = "/bin/sh"
        if env:
            full_env.update(env)
        return self.backend.run(args, cwd=cwd, env=full_env, **kwargs)

    def save_status(self, status):
        """Save a dictionary of status information about this build.

        This will be picked up by the build manager and included in XML-RPC
        status responses.
        """
        status_path = os.path.join(self.backend.build_path, "status")
        with open("%s.tmp" % status_path, "w") as status_file:
            json.dump(status, status_file)
        os.rename("%s.tmp" % status_path, status_path)

    def install(self):
        logger.info("Running install phase...")
        deps = ["snapcraft"]
        if self.args.backend == "lxd":
            # udev is installed explicitly to work around
            # https://bugs.launchpad.net/snapd/+bug/1731519.
            for dep in "snapd", "fuse", "squashfuse", "udev":
                if self.backend.is_package_available(dep):
                    deps.append(dep)
        if self.args.branch is not None:
            deps.append("bzr")
        else:
            deps.append("git")
        if self.args.proxy_url:
            deps.extend(["python3", "socat"])
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.args.proxy_url:
            self.backend.copy_in(
                os.path.join(self.slavebin, "snap-git-proxy"),
                "/usr/local/bin/snap-git-proxy")

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = OrderedDict()
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        if self.args.branch is not None:
            self.run_build_command(['ls', '/build'])
            cmd = ["bzr", "branch", self.args.branch, self.args.name]
            if not self.ssl_verify:
                cmd.insert(1, "-Ossl.cert_reqs=none")
        else:
            assert self.args.git_repository is not None
            cmd = ["git", "clone"]
            if self.args.git_path is not None:
                cmd.extend(["-b", self.args.git_path])
            cmd.extend([self.args.git_repository, self.args.name])
            if not self.ssl_verify:
                env["GIT_SSL_NO_VERIFY"] = "1"
        self.run_build_command(cmd, env=env)
        if self.args.git_repository is not None:
            try:
                self.run_build_command(
                    ["git", "-C", self.args.name,
                     "submodule", "update", "--init", "--recursive"],
                    env=env)
            except subprocess.CalledProcessError as e:
                logger.error(
                    "'git submodule update --init --recursive failed with "
                    "exit code %s (build may fail later)" % e.returncode)
        status = {}
        if self.args.branch is not None:
            status["revision_id"] = self.run_build_command(
                ["bzr", "revno", self.args.name],
                get_output=True).rstrip("\n")
        else:
            rev = (
                self.args.git_path
                if self.args.git_path is not None else "HEAD")
            status["revision_id"] = self.run_build_command(
                ["git", "-C", self.args.name, "rev-parse", rev],
                get_output=True).rstrip("\n")
        self.save_status(status)

    def pull(self):
        """Run pull phase."""
        logger.info("Running pull phase...")
        env = OrderedDict()
        env["SNAPCRAFT_LOCAL_SOURCES"] = "1"
        env["SNAPCRAFT_SETUP_CORE"] = "1"
        # XXX cjwatson 2017-11-24: Once we support building private snaps,
        # we'll need to make this optional in some way.
        env["SNAPCRAFT_BUILD_INFO"] = "1"
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        self.run_build_command(
            ["snapcraft", "pull"],
            cwd=os.path.join("/build", self.args.name),
            env=env)

    def build(self):
        """Run all build, stage and snap phases."""
        logger.info("Running build phase...")
        env = OrderedDict()
        # XXX cjwatson 2017-11-24: Once we support building private snaps,
        # we'll need to make this optional in some way.
        env["SNAPCRAFT_BUILD_INFO"] = "1"
        if self.args.proxy_url:
            env["http_proxy"] = self.args.proxy_url
            env["https_proxy"] = self.args.proxy_url
            env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        self.run_build_command(
            ["snapcraft"],
            cwd=os.path.join("/build", self.args.name),
            env=env)

    def revoke_token(self):
        """Revoke builder proxy token."""
        logger.info("Revoking proxy token...")
        url = urlparse(self.args.proxy_url)
        auth = '{}:{}'.format(url.username, url.password)
        headers = {
            'Authorization': 'Basic {}'.format(base64.b64encode(auth))
            }
        req = Request(self.args.revocation_endpoint, None, headers)
        req.get_method = lambda: 'DELETE'
        try:
            urlopen(req)
        except (HTTPError, URLError):
            logger.exception('Unable to revoke token for %s', url.username)

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
        finally:
            if self.args.revocation_endpoint is not None:
                self.revoke_token()
        return 0
