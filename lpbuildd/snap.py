# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import json
import os
import sys

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

    backend_name = "lxd"
    initial_build_state = SnapBuildState.BUILD_SNAP

    @property
    def needs_sanitized_logs(self):
        return True

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.name = extra_args["name"]
        self.branch = extra_args.get("branch")
        self.git_repository = extra_args.get("git_repository")
        self.git_path = extra_args.get("git_path")
        self.proxy_url = extra_args.get("proxy_url")
        self.revocation_endpoint = extra_args.get("revocation_endpoint")

        super(SnapBuildManager, self).initiate(files, chroot, extra_args)

    def status(self):
        status_path = get_build_path(self.home, self._buildid, "status")
        try:
            with open(status_path) as status_file:
                return json.load(status_file)
        except IOError:
            pass
        except Exception as e:
            print(
                "Error deserialising status from buildsnap: %s" % e,
                file=sys.stderr)
        return {}

    def doRunBuild(self):
        """Run the process to build the snap."""
        args = []
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
        self.runTargetSubProcess("buildsnap", *args)

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
        output_path = os.path.join("/build", self.name)
        if not self.backend.path_exists(output_path):
            return
        for entry in sorted(self.backend.listdir(output_path)):
            path = os.path.join(output_path, entry)
            if self.backend.islink(path):
                continue
            if entry.endswith(".snap") or entry.endswith(".manifest"):
                self.addWaitingFileFromBackend(path)
