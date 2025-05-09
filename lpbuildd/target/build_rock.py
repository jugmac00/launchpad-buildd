import logging
import os

from lpbuildd.target.backend import check_path_escape
from lpbuildd.target.build_snap import SnapChannelsAction
from lpbuildd.target.operation import Operation
from lpbuildd.target.proxy import BuilderProxyOperationMixin
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin

RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201

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
            "--use-fetch-service",
            default=False,
            action="store_true",
            help="use the fetch service instead of the builder proxy",
        )
        parser.add_argument(
            "--fetch-service-mitm-certificate",
            type=str,
            help="content of the ca certificate",
        )
        parser.add_argument(
            "--launchpad-instance",
            type=str,
            help="launchpad instance (production, qastaging, staging, devel).",
        )
        parser.add_argument(
            "--launchpad-server-url",
            type=str,
            help="launchpad server url.",
        )

    def __init__(self, args, parser):
        super().__init__(args, parser)
        self.buildd_path = os.path.join("/home/buildd", self.args.name)

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
        # repo-overlay features requires dirmngr to access OpenPGP keyservers
        # otherwise the build errors out with unknown GPG key error
        deps.extend(["dirmngr"])
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
            self.install_apt_proxy()
            self.delete_apt_cache()
            self.install_mitm_certificate()
            self.install_snapd_proxy(proxy_url=self.args.proxy_url)
            self.backend.run(["apt-get", "-y", "update"])
            self.restart_snapd()
            self.configure_git_protocol_v2()

        # With classic confinement, the snap can access the whole system.
        # We could build the rock in /build, but we are using /home/buildd
        # for consistency with other build types.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = self.build_proxy_environment(
            proxy_url=self.args.proxy_url,
            use_fetch_service=self.args.use_fetch_service,
        )
        # using the fetch service requires shallow clones
        git_shallow_clone = bool(self.args.use_fetch_service)
        self.vcs_fetch(
            self.args.name,
            cwd="/home/buildd",
            env=env,
            git_shallow_clone_with_single_branch=git_shallow_clone,
        )
        self.vcs_update_status(self.buildd_path)

    def build(self):
        logger.info("Running build phase...")
        build_context_path = os.path.join(
            "/home/buildd", self.args.name, self.args.build_path
        )
        check_path_escape(self.buildd_path, build_context_path)
        env = self.build_proxy_environment(
            proxy_url=self.args.proxy_url,
            use_fetch_service=self.args.use_fetch_service,
        )
        if self.args.launchpad_instance:
            env["LAUNCHPAD_INSTANCE"] = self.args.launchpad_instance
        if self.args.launchpad_server_url:
            env["LAUNCHPAD_SERVER_URL"] = self.args.launchpad_server_url
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
