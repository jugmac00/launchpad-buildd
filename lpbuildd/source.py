import os

from lpbuildd.debian import DebianBuildManager, DebianBuildState
from lpbuildd.proxy import BuildManagerProxyMixin

RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


class SourceBuildState(DebianBuildState):
    BUILD_SOURCE = "BUILD_SOURCE"


class SourceBuildManager(BuildManagerProxyMixin, DebianBuildManager):
    """Build a source."""

    backend_name = "lxd"
    initial_build_state = SourceBuildState.BUILD_SOURCE

    @property
    def needs_sanitized_logs(self):
        return True
    
    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.name = extra_args["name"]
        self.branch = extra_args.get("branch")
        self.git_repository = extra_args.get("git_repository")
        self.git_path = extra_args.get("git_path")
        self.build_path = extra_args.get("build_path")
        self.channels = extra_args.get("channels", {})
        self.proxy_url = extra_args.get("proxy_url")
        self.revocation_endpoint = extra_args.get("revocation_endpoint")
        self.proxy_service = None

        super().initiate(files, chroot, extra_args)

    def doRunBuild(self):
        """Run the process to build the source."""
        args = []
        args.extend(self.startProxy())
        if self.revocation_endpoint:
            args.extend(["--revocation-endpoint", self.revocation_endpoint])
        for snap, channel in sorted(self.channels.items()):
            args.extend(["--channel", f"{snap}={channel}"])
        if self.branch is not None:
            args.extend(["--branch", self.branch])
        if self.git_repository is not None:
            args.extend(["--git-repository", self.git_repository])
        if self.git_path is not None:
            args.extend(["--git-path", self.git_path])
        if self.build_path is not None:
            args.extend(["--build-path", self.build_path])
        args.append(self.name)
        self.runTargetSubProcess("build-source", *args)

    def iterate_BUILD_SOURCE(self, retcode):
        """Finished building the source."""
        self.stopProxy()
        self.revokeProxyToken()
        if retcode == RETCODE_SUCCESS:
            print("[source] Returning build status: OK")
            return self.deferGatherResults()
        elif (
            retcode >= RETCODE_FAILURE_INSTALL
            and retcode <= RETCODE_FAILURE_BUILD
        ):
            if not self.alreadyfailed:
                self._builder.buildFail()
                print("[source] Returning build status: Builder failed.")
            self.alreadyfailed = True
        else:
            if not self.alreadyfailed:
                self._builder.buildFail()
                print("[source] Returning build status: Build failed.")
            self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_BUILD_SOURCE(self, retcode):
        """Finished reaping after building the source."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache."""
        output_path = os.path.join("/home/buildd", self.name)
        if self.build_path is not None:
            output_path = os.path.join(output_path, self.build_path)
        if self.backend.path_exists(output_path):
            for entry in sorted(self.backend.listdir(output_path)):
                path = os.path.join(output_path, entry)
                if self.backend.islink(path):
                    continue
                if entry.endswith(".tar.xz"):
                    self.addWaitingFileFromBackend(path)
