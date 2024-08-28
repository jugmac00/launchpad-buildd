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


class BuildSource(
    BuilderProxyOperationMixin,
    VCSOperationMixin,
    SnapStoreOperationMixin,
    Operation,
):
    description = "Build a source."

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
            "--build-path", default=".", help="location of source to build."
        )
        parser.add_argument("name", help="name of source to build")

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
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.backend.supports_snapd:
            self.snap_store_set_proxy()
        for snap_name, channel in sorted(self.args.channels.items()):
            # sourcecraft is handled separately, since it requires --classic,
            # which disables all sandboxing to ensure it runs with no strict
            # confinement.
            if snap_name != "sourcecraft":
                self.backend.run(
                    ["snap", "install", "--channel=%s" % channel, snap_name]
                )
        if "sourcecraft" in self.args.channels:
            self.backend.run(
                [
                    "snap",
                    "install",
                    "--classic",
                    "--channel=%s" % self.args.channels["sourcecraft"],
                    "sourcecraft",
                ]
            )
        else:
            self.backend.run(["snap", "install", "--classic", "--channel=latest/edge/craftctl", "sourcecraft"])
        # With classic confinement, the snap can access the whole system.
        # We could build the source in /build, but we are using /home/buildd
        # for consistency with other build types.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        self.vcs_fetch(self.args.name, cwd="/home/buildd", env=env)
        self.vcs_update_status(self.buildd_path)

    def build(self):
        logger.info("Running build phase...")
        build_context_path = os.path.join(
            "/home/buildd", self.args.name, self.args.build_path
        )
        check_path_escape(self.buildd_path, build_context_path)
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        args = ["sourcecraft", "pack", "-v"]
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
