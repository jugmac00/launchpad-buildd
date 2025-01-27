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

MITM_CERTIFICATE_PATH = "/usr/local/share/ca-certificates/local-ca.crt"

logger = logging.getLogger(__name__)


class BuildCraft(
    BuilderProxyOperationMixin,
    VCSOperationMixin,
    SnapStoreOperationMixin,
    Operation,
):
    description = "Build a craft."

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
            "--build-path",
            default=".",
            help="location of sourcecraft package to build.",
        )
        parser.add_argument(
            "name",
            help="name of sourcecraft package to build",
        )
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
            self.backend.run(
                [
                    "snap",
                    "install",
                    "--classic",
                    "--channel=latest/edge/craftctl",
                    "sourcecraft",
                ]
            )
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
        # We could build the craft in /build, but we are using /home/buildd
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

    def setup_cargo_credentials(self):
        """Set up Cargo credential files if needed."""
        env_vars = dict(
            pair.split("=", maxsplit=1) 
            for pair in self.args.environment_variables
        )
        
        # Check if we have any cargo-related variables
        cargo_vars = {k: v for k, v in env_vars.items() if k.startswith("CARGO_")}
        if not cargo_vars:
            return

        # Create .cargo directory
        cargo_dir = os.path.join(self.buildd_path, ".cargo")
        self.backend.run(["mkdir", "-p", cargo_dir])

        # Parse registry URLs and tokens
        registries = {}
        for key, value in cargo_vars.items():
            if key.endswith("_URL"):
                registry_name = key[6:-4].lower()  # Remove CARGO_ and _URL
                registries.setdefault(registry_name, {})["url"] = value
            elif key.endswith("_TOKEN"):
                registry_name = key[6:-6].lower()  # Remove CARGO_ and _TOKEN
                registries.setdefault(registry_name, {})["token"] = value

        # Create config.toml manually
        config_toml = '[registry]\nglobal-credential-providers = ["cargo:token"]\n\n'
        
        # Add registry sections
        for name, reg in registries.items():
            config_toml += f'[registries.{name}-stable-local]\nindex = "{reg["url"]}"\n\n'
        
        # Add source.crates-io section
        first_registry = next(iter(registries.keys()), "crates-io")
        config_toml += f'[source.crates-io]\nreplace-with = "{first_registry}"\n'

        with self.backend.open(os.path.join(cargo_dir, "config.toml"), "w") as f:
            f.write(config_toml)

        # Create credentials.toml manually
        creds_toml = ""
        for name, reg in registries.items():
            if "token" in reg:
                creds_toml += f'[registries.{name}-stable-local]\ntoken = "Bearer {reg["token"]}"\n\n'

        with self.backend.open(os.path.join(cargo_dir, "credentials.toml"), "w") as f:
            f.write(creds_toml)

    def setup_maven_credentials(self):
        """Set up Maven credential files if needed."""
        env_vars = dict(
            pair.split("=", maxsplit=1) 
            for pair in self.args.environment_variables
        )
        
        # Check if we have any maven-related variables
        maven_vars = {k: v for k, v in env_vars.items() if k.startswith("MAVEN_")}
        if not maven_vars:
            return

        # Create .m2 directory
        m2_dir = os.path.join(self.buildd_path, ".m2")
        self.backend.run(["mkdir", "-p", m2_dir])

        # Parse repository URLs and credentials
        repositories = {}
        for key, value in maven_vars.items():
            if key.endswith("_URL"):
                repo_name = key[6:-4].lower()  # Remove MAVEN_ and _URL
                repositories.setdefault(repo_name, {})["url"] = value
            elif key.endswith("_USERNAME"):
                repo_name = key[6:-9].lower()  # Remove MAVEN_ and _USERNAME
                repositories.setdefault(repo_name, {})["username"] = value
            elif key.endswith("_PASSWORD"):
                repo_name = key[6:-9].lower()  # Remove MAVEN_ and _PASSWORD
                repositories.setdefault(repo_name, {})["password"] = value

        # Create settings.xml
        settings_xml = """<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.0.0 http://maven.apache.org/xsd/settings-1.0.0.xsd">
    <servers>
"""
        for name, repo in repositories.items():
            if "username" in repo and "password" in repo:
                settings_xml += f"""        <server>
            <id>{name}</id>
            <username>{repo['username']}</username>
            <password>{repo['password']}</password>
        </server>
"""
        settings_xml += """    </servers>
</settings>
"""
        with self.backend.open(os.path.join(m2_dir, "settings.xml"), "w") as f:
            f.write(settings_xml)

    def build(self):
        """Running build phase..."""
        # Set up credential files before building
        self.setup_cargo_credentials()
        self.setup_maven_credentials()

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
        args = ["sourcecraft", "pack", "-v", "--destructive-mode"]
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
