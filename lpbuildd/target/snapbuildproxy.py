# Copyright 2019-2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import os


class SnapBuildProxyOperationMixin:
    """Methods supporting the build time HTTP proxy for snap and OCI builds."""

    @classmethod
    def add_arguments(cls, parser):
        super(SnapBuildProxyOperationMixin, cls).add_arguments(parser)
        parser.add_argument("--proxy-url", help="builder proxy url")
        parser.add_argument(
            "--revocation-endpoint",
            help="builder proxy token revocation endpoint")

    @property
    def proxy_deps(self):
        return ["python3", "socat"]

    def install_git_proxy(self):
        self.backend.copy_in(
            os.path.join(self.bin, "snap-git-proxy"),
            "/usr/local/bin/snap-git-proxy")

    def build_proxy_environment(self, proxy_url=None, env=None):
        """Extend a command environment to include http proxy variables."""
        full_env = OrderedDict()
        if env:
            full_env.update(env)
        if proxy_url:
            full_env["http_proxy"] = self.args.proxy_url
            full_env["https_proxy"] = self.args.proxy_url
            full_env["GIT_PROXY_COMMAND"] = "/usr/local/bin/snap-git-proxy"
        return full_env
