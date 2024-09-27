# Copyright 2019-2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64
import os
import sys
from collections import OrderedDict
from textwrap import dedent
from urllib.parse import urlparse


class BuilderProxyOperationMixin:
    """Methods supporting the build time HTTP proxy for certain build types."""

    mitm_certificate_path = "/usr/local/share/ca-certificates/local-ca.crt"

    def __init__(self, args, parser):
        super().__init__(args, parser)
        self.bin = os.path.dirname(sys.argv[0])

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument("--proxy-url", help="builder proxy url")
        parser.add_argument(
            "--revocation-endpoint",
            help="builder proxy token revocation endpoint",
        )

    @property
    def proxy_deps(self):
        return ["python3", "socat"]

    def install_git_proxy(self):
        self.backend.copy_in(
            os.path.join(self.bin, "lpbuildd-git-proxy"),
            "/usr/local/bin/lpbuildd-git-proxy",
        )

    def install_apt_proxy(self):
        """Install the apt proxy

        This is necesessary so the fetch service can be used by the
        apt service.
        """
        if self.args.proxy_url:
            with self.backend.open(
                "/etc/apt/apt.conf.d/99proxy", mode="w+"
            ) as apt_proxy_conf:
                print(
                    f'Acquire::http::Proxy "{self.args.proxy_url}";\n'
                    f'Acquire::https::Proxy "{self.args.proxy_url}";\n',
                    file=apt_proxy_conf,
                )
                os.fchmod(apt_proxy_conf.fileno(), 0o644)

    def install_mitm_certificate(self):
        """Install ca certificate for the fetch service

        This is necessary so the fetch service can man-in-the-middle all
        requests when fetching dependencies.
        """
        with self.backend.open(
            self.mitm_certificate_path, mode="wb"
        ) as local_ca_cert:
            # Certificate is passed as a Base64 encoded string.
            # It's encoded using `base64 -w0` on the cert file.
            decoded_certificate = base64.b64decode(
                self.args.fetch_service_mitm_certificate.encode("ASCII")
            )
            local_ca_cert.write(decoded_certificate)
            os.fchmod(local_ca_cert.fileno(), 0o644)
        self.backend.run(["update-ca-certificates"])

    def install_snapd_proxy(self, proxy_url):
        """Install snapd proxy

        This is necessary so the proxy can communicate properly
        with snapcraft.
        """
        if proxy_url:
            self.backend.run(
                ["snap", "set", "system", f"proxy.http={proxy_url}"]
            )
            self.backend.run(
                ["snap", "set", "system", f"proxy.https={proxy_url}"]
            )

    def install_svn_servers(self, proxy_url):
        proxy = urlparse(proxy_url)
        svn_servers = dedent(
            f"""\
            [global]
            http-proxy-host = {proxy.hostname}
            http-proxy-port = {proxy.port}
            """
        )
        # We should never end up with an authenticated proxy here since
        # lpbuildd.snap deals with it, but it's almost as easy to just
        # handle it as to assert that we don't need to.
        if proxy.username:
            svn_servers += f"http-proxy-username = {proxy.username}\n"
        if proxy.password:
            svn_servers += f"http-proxy-password = {proxy.password}\n"
        self.backend.run(["mkdir", "-p", "/root/.subversion"])
        with self.backend.open(
            "/root/.subversion/servers", mode="w+"
        ) as svn_servers_file:
            svn_servers_file.write(svn_servers)
            os.fchmod(svn_servers_file.fileno(), 0o644)

    def build_proxy_environment(
        self, proxy_url=None, env=None, use_fetch_service=False
    ):
        """Extend a command environment to include http proxy variables."""
        full_env = OrderedDict()
        if env:
            full_env.update(env)
        if proxy_url:
            full_env["http_proxy"] = self.args.proxy_url
            full_env["https_proxy"] = self.args.proxy_url
            full_env["GIT_PROXY_COMMAND"] = "/usr/local/bin/lpbuildd-git-proxy"
            # Avoid needing to keep track of snap store CDNs in proxy
            # configuration.
            full_env["SNAPPY_STORE_NO_CDN"] = "1"
        # Avoid circular import using __class__.__name__
        if use_fetch_service and self.__class__.__name__ == "BuildRock":
            full_env["CARGO_HTTP_CAINFO"] = self.mitm_certificate_path
            full_env["GOPROXY"] = "direct"

        return full_env

    def restart_snapd(self):
        # This is required to pick up the certificate
        self.backend.run(["systemctl", "restart", "snapd"])

    def delete_apt_cache(self):

        self.backend.run(["rm", "-rf", "/var/lib/apt/lists/*"])

    def configure_git_protocol_v2(self):
        if self.backend.series == "focal":
            self.backend.run(
                ["git", "config", "--global", "protocol.version", "2"]
            )
