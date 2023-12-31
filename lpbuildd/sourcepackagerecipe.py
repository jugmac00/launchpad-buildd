# Copyright 2010-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).
# pylint: disable-msg=E1002

"""The manager class for building packages from recipes."""

import os
import re
import subprocess

from lpbuildd.builder import get_build_path
from lpbuildd.debian import DebianBuildManager, DebianBuildState

RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD_TREE = 201
RETCODE_FAILURE_INSTALL_BUILD_DEPS = 202
RETCODE_FAILURE_BUILD_SOURCE_PACKAGE = 203


def get_chroot_path(home, build_id, *extra):
    """Return a path within the chroot.

    :param home: The user's home directory.
    :param build_id: The build_id of the build.
    :param extra: Additional path elements.
    """
    return get_build_path(
        home, build_id, "chroot-autobuild", os.environ["HOME"][1:], *extra
    )


class SourcePackageRecipeBuildState(DebianBuildState):
    """The set of states that a recipe build can be in."""

    BUILD_RECIPE = "BUILD_RECIPE"


class SourcePackageRecipeBuildManager(DebianBuildManager):
    """Build a source package from a bzr-builder recipe."""

    initial_build_state = SourcePackageRecipeBuildState.BUILD_RECIPE

    def __init__(self, builder, buildid):
        """Constructor.

        :param builder: A builder.
        :param buildid: The id of the build (a str).
        """
        DebianBuildManager.__init__(self, builder, buildid)
        self.build_recipe_path = os.path.join(self._bin, "buildrecipe")

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot.

        :param files: The files sent by the manager with the request.
        :param chroot: The sha1sum of the chroot to use.
        :param extra_args: A dict of extra arguments.
        """
        self.recipe_text = extra_args["recipe_text"]
        self.suite = extra_args["suite"]
        self.component = extra_args["ogrecomponent"]
        self.author_name = extra_args["author_name"]
        self.author_email = extra_args["author_email"]
        self.archive_purpose = extra_args["archive_purpose"]
        self.git = extra_args.get("git", False)

        super().initiate(files, chroot, extra_args)

    def doRunBuild(self):
        """Run the build process to build the source package."""
        work_dir = os.path.join(os.environ["HOME"], "work")
        self.backend.run(["mkdir", "-p", work_dir])
        # buildrecipe needs to be able to write directly to the work
        # directory.  (That directory needs to be inside the chroot so that
        # buildrecipe can run dpkg-buildpackage on it from inside the
        # chroot.)
        subprocess.run(
            [
                "sudo",
                "chown",
                "-R",
                "buildd:",
                get_chroot_path(self.home, self._buildid, "work"),
            ],
            check=True,
        )
        with self.backend.open(
            os.path.join(work_dir, "recipe"), "w"
        ) as recipe_file:
            recipe_file.write(self.recipe_text)
        args = ["buildrecipe"]
        if self.git:
            args.append("--git")
        args.extend(
            [
                self._buildid,
                self.author_name.encode("utf-8"),
                self.author_email,
                self.suite,
                self.series,
                self.component,
                self.archive_purpose,
            ]
        )
        self.runSubProcess(self.build_recipe_path, args)

    def iterate_BUILD_RECIPE(self, retcode):
        """Move from BUILD_RECIPE to the next logical state."""
        if retcode == RETCODE_SUCCESS:
            print("Returning build status: OK")
            return self.deferGatherResults()
        elif retcode == RETCODE_FAILURE_INSTALL_BUILD_DEPS:
            if not self.alreadyfailed:
                rx = (
                    r"The following packages have unmet dependencies:\n"
                    r".*: Depends: ([^ ]*( \([^)]*\))?)"
                )
                _, mo = self.searchLogContents([[rx, re.M]])
                if mo:
                    missing_dep = mo.group(1).decode("UTF-8", "replace")
                    self._builder.depFail(missing_dep)
                    print("Returning build status: DEPFAIL")
                    print("Dependencies: " + missing_dep)
                else:
                    print("Returning build status: Build failed")
                    self._builder.buildFail()
            self.alreadyfailed = True
        elif (
            retcode >= RETCODE_FAILURE_INSTALL
            and retcode <= RETCODE_FAILURE_BUILD_SOURCE_PACKAGE
        ):
            # XXX AaronBentley 2009-01-13: We should handle depwait separately
            if not self.alreadyfailed:
                self._builder.buildFail()
                print("Returning build status: Build failed.")
            self.alreadyfailed = True
        else:
            if not self.alreadyfailed:
                self._builder.builderFail()
                print("Returning build status: Builder failed.")
            self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_BUILD_RECIPE(self, retcode):
        """Finished reaping after recipe building."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    def getChangesFilename(self):
        """Return the path to the changes file."""
        work_path = get_build_path(self.home, self._buildid)
        for name in os.listdir(work_path):
            if name.endswith("_source.changes"):
                return os.path.join(work_path, name)

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache.

        The primary file we care about is the .changes file.
        The manifest is also a useful record.
        """
        DebianBuildManager.gatherResults(self)
        self._builder.addWaitingFile(
            get_build_path(self.home, self._buildid, "manifest")
        )
