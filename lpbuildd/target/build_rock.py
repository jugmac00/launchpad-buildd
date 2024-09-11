import logging
import os
import base64

from lpbuildd.target.backend import check_path_escape
from lpbuildd.target.build_snap import SnapChannelsAction
from lpbuildd.target.operation import Operation
from lpbuildd.target.proxy import BuilderProxyOperationMixin
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin

RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201
MITM_CERTIFICATE_PATH = "/usr/local/share/ca-certificates/local-ca.crt"

logger = logging.getLogger(__name__)


class BuildRock(
    BuilderProxyOperationMixin,
    VCSOperationMixin,
    SnapStoreOperationMixin,
    Operation,
):
    description = "Build a rock."

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--channel",
            action=SnapChannelsAction,
            metavar="SNAP=CHANNEL",
            dest="channels",
            default={},
            help="install SNAP from CHANNEL",
        )
        parser.add_argument(
            "--build-path", default=".", help="location of rock to build."
        )
        parser.add_argument("name", help="name of rock to build")
        parser.add_argument(
            "--use_fetch_service",
            default=False,
            action="store_true",
            help="use the fetch service instead of the builder proxy",
        )
        parser.add_argument(
            "--fetch-service-mitm-certificate",
            type=str,
            help="content of the ca certificate",
        )

    def __init__(self, args, parser):
        super().__init__(args, parser)
        self.buildd_path = os.path.join("/home/buildd", self.args.name)

    def install_mitm_certificate(self):
        """Install ca certificate for the fetch service

        This is necessary so the fetch service can man-in-the-middle all
        requests when fetching dependencies.
        """
        with self.backend.open(
            MITM_CERTIFICATE_PATH, mode="wb"
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

    def build_rock_proxy_environment(self, env):
        """Extend a command environment to include rockcraftproxy variables."""
        env["CARGO_HTTP_CAINFO"] = MITM_CERTIFICATE_PATH
        env["GOPROXY"] = "direct"
        return env

    def restart_snapd(self):
        # This is required to pick up the certificate
        self.backend.run(["systemctl", "restart", "snapd"])

    def install(self):
        logger.info("Running install phase")
        deps = []
        if self.args.proxy_url:
            deps.extend(self.proxy_deps)
            self.install_git_proxy()
        if self.backend.supports_snapd:
            # udev is installed explicitly to work around
            # https://bugs.launchpad.net/snapd/+bug/1731519.
            # Low maintenance: we can keep udevs as a dependency
            # since it is a low-level system dependency,
            # and since it might be broken for older versions.
            for dep in "snapd", "fuse", "squashfuse", "udev":
                if self.backend.is_package_available(dep):
                    deps.append(dep)
        deps.extend(self.vcs_deps)
        # See charmcraft.provider.CharmcraftBuilddBaseConfiguration.setup.
        deps.extend(
            [
                "python3-pip",
                "python3-setuptools",
            ]
        )
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.backend.supports_snapd:
            self.snap_store_set_proxy()
        for snap_name, channel in sorted(self.args.channels.items()):
            # rockcraft is handled separately, since it requires --classic,
            # which disables all sandboxing to ensure it runs with no strict
            # confinement.
            if snap_name != "rockcraft":
                self.backend.run(
                    ["snap", "install", "--channel=%s" % channel, snap_name]
                )
        if "rockcraft" in self.args.channels:
            self.backend.run(
                [
                    "snap",
                    "install",
                    "--classic",
                    "--channel=%s" % self.args.channels["rockcraft"],
                    "rockcraft",
                ]
            )
        else:
            self.backend.run(["snap", "install", "--classic", "rockcraft"])

        if self.args.use_fetch_service:
            # Deleting apt cache /var/lib/apt/lists before
            # installing the fetch service
            self.backend.run(
                ["rm", "-rf", "/var/lib/apt/lists/*"]
            )
            self.install_mitm_certificate()
            self.install_snapd_proxy(proxy_url=self.args.proxy_url)
            self.backend.run(["apt-get", "-y", "update"])
            self.restart_snapd()

        # With classic confinement, the snap can access the whole system.
        # We could build the rock in /build, but we are using /home/buildd
        # for consistency with other build types.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        if self.args.use_fetch_service:
            env = self.build_rock_proxy_environment(env)
        self.vcs_fetch(self.args.name, cwd="/home/buildd", env=env)
        self.vcs_update_status(self.buildd_path)

    def build(self):
        logger.info("Running build phase...")
        build_context_path = os.path.join(
            "/home/buildd", self.args.name, self.args.build_path
        )
        check_path_escape(self.buildd_path, build_context_path)
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        if self.args.use_fetch_service:
            env = self.build_rock_proxy_environment(env)
        args = ["rockcraft", "pack", "-v", "--destructive-mode"]
        self.run_build_command(args, env=env, cwd=build_context_path)

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
