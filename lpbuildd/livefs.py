# Copyright 2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import shutil

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    get_build_path,
    )


class LiveFilesystemBuildState(DebianBuildState):
    BUILD_LIVEFS = "BUILD_LIVEFS"


class LiveFilesystemBuildManager(DebianBuildManager):
    """Build a live filesystem."""

    initial_build_state = LiveFilesystemBuildState.BUILD_LIVEFS

    def __init__(self, slave, buildid, **kwargs):
        DebianBuildManager.__init__(self, slave, buildid, **kwargs)
        self.build_livefs_path = slave._config.get(
            "livefilesystemmanager", "buildlivefspath")

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.build_path = get_build_path(
            self.home, self._buildid, "chroot-autobuild", "build")
        if os.path.isdir(self.build_path):
            shutil.rmtree(self.build_path)

        self.subarch = extra_args.get("subarch")
        self.project = extra_args["project"]
        self.subproject = extra_args.get("subproject")
        self.suite = extra_args["suite"]
        self.image_format = extra_args.get("image_format")
        self.proposed = extra_args.get("proposed", False)
        self.locale = extra_args.get("locale")

        super(LiveFilesystemBuildManager, self).initiate(
            files, chroot, extra_args)

    def doRunBuild(self):
        """Run the process to build the live filesystem."""
        args = [
            "buildlivefs",
            "--build-id", self._buildid,
            "--arch", self.arch_tag,
            ]
        if self.subarch:
            args.extend(["--subarch", self.subarch])
        args.extend(["--project", self.project])
        if self.subproject:
            args.extend(["--subproject", self.subproject])
        args.extend(["--suite", self.suite])
        if self.image_format:
            args.extend(["--image-format", self.image_format])
        if self.proposed:
            args.append("--proposed")
        if self.locale:
            args.extend(["--locale", self.locale])
        self.runSubProcess(self.build_livefs_path, args)

    def iterate_BUILD_LIVEFS(self, retcode):
        """Finished building the live filesystem."""
        if retcode == 0:
            self.gatherResults()
            print("Returning build status: OK")
        else:
            if not self.alreadyfailed:
                self._slave.buildFail()
                print("Returning build status: Build failed.")
            self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_BUILD_LIVEFS(self, retcode):
        """Finished reaping after building the live filesystem."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache."""
        for entry in sorted(os.listdir(self.build_path)):
            if entry.startswith("livecd."):
                self._slave.addWaitingFile(
                    os.path.join(self.build_path, entry))
