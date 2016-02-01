# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import shutil

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    get_build_path,
    )


RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


class SnapBuildState(DebianBuildState):
    BUILD_SNAP = "BUILD_SNAP"


class SnapBuildManager(DebianBuildManager):
    """Build a snap."""

    initial_build_state = SnapBuildState.BUILD_SNAP

    def __init__(self, slave, buildid, **kwargs):
        super(SnapBuildManager, self).__init__(slave, buildid, **kwargs)
        self.build_snap_path = os.path.join(self._slavebin, "buildsnap")

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.build_path = get_build_path(
            self.home, self._buildid, "chroot-autobuild", "build")
        if os.path.isdir(self.build_path):
            shutil.rmtree(self.build_path)

        self.name = extra_args["name"]
        self.branch = extra_args.get("branch")
        self.git_repository = extra_args.get("git_repository")
        self.git_path = extra_args.get("git_path")
        self.proxy_url = extra_args.get("proxy_url")
        self.revocation_endpoint = extra_args.get("revocation_endpoint")

        super(SnapBuildManager, self).initiate(files, chroot, extra_args)

    def doRunBuild(self):
        """Run the process to build the snap."""
        args = [
            "buildsnap",
            "--build-id", self._buildid,
            "--arch", self.arch_tag,
            ]
        if self.proxy_url:
            args.extend(["--proxy-url", self.proxy_url])
        if self.revocation_endpoint:
            args.extend(["--revocation-endpoint", self.revocation_endpoint])
        if self.branch is not None:
            args.extend(["--branch", self.branch])
        if self.git_repository is not None:
            args.extend(["--git-repository", self.git_repository])
        if self.git_path is not None:
            args.extend(["--git-path", self.git_path])
        args.append(self.name)
        self.runSubProcess(self.build_snap_path, args)

    def iterate_BUILD_SNAP(self, retcode):
        """Finished building the snap."""
        if retcode == RETCODE_SUCCESS:
            self.gatherResults()
            print("Returning build status: OK")
        elif (retcode >= RETCODE_FAILURE_INSTALL and
              retcode <= RETCODE_FAILURE_BUILD):
            if not self.alreadyfailed:
                self._slave.buildFail()
                print("Returning build status: Build failed.")
            self.alreadyfailed = True
        else:
            if not self.alreadyfailed:
                self._slave.builderFail()
                print("Returning build status: Builder failed.")
            self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_BUILD_SNAP(self, retcode):
        """Finished reaping after building the snap."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache."""
        output_path = os.path.join(self.build_path, self.name)
        if not os.path.exists(output_path):
            return
        for entry in sorted(os.listdir(output_path)):
            path = os.path.join(output_path, entry)
            if entry.endswith(".snap") and not os.path.islink(path):
                self._slave.addWaitingFile(path)
