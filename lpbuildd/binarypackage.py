# Copyright 2009, 2010 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).


import os
import re

from lpbuildd.debian import DebianBuildManager, DebianBuildState


class SBuildExitCodes:
    """SBUILD process result codes."""
    OK = 0
    FAILED = 1
    ATTEMPTED = 2
    GIVENBACK = 3
    BUILDERFAIL = 4


class BuildLogRegexes:
    """Build log regexes for performing actions based on regexes, and extracting dependencies for auto dep-waits"""
    GIVENBACK = [
        ("^E: There are problems and -y was used without --force-yes"),
        ]
    DEPFAIL = {
        "(?P<pk>[\-+.\w]+)\(inst [^ ]+ ! >> wanted (?P<v>[\-.+\w:~]+)\)": "\g<pk> (>> \g<v>)",
        "(?P<pk>[\-+.\w]+)\(inst [^ ]+ ! >?= wanted (?P<v>[\-.+\w:~]+)\)": "\g<pk> (>= \g<v>)",
        "(?s)^E: Couldn't find package (?P<pk>[\-+.\w]+)(?!.*^E: Couldn't find package)": "\g<pk>",
        "(?s)^E: Package '?(?P<pk>[\-+.\w]+)'? has no installation candidate(?!.*^E: Package)": "\g<pk>",
        "(?s)^E: Unable to locate package (?P<pk>[\-+.\w]+)(?!.*^E: Unable to locate package)": "\g<pk>",
        }


class BinaryPackageBuildState(DebianBuildState):
    SBUILD = "SBUILD"


class BinaryPackageBuildManager(DebianBuildManager):
    """Handle buildd building for a debian style binary package build"""

    initial_build_state = BinaryPackageBuildState.SBUILD

    def __init__(self, slave, buildid, **kwargs):
        DebianBuildManager.__init__(self, slave, buildid, **kwargs)
        self._sbuildpath = slave._config.get("binarypackagemanager", "sbuildpath")

    @property
    def chroot_path(self):
        return os.path.join(
            self.home, "build-" + self._buildid, 'chroot-autobuild')

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""

        self._dscfile = None
        for f in files:
            if f.endswith(".dsc"):
                self._dscfile = f
        if self._dscfile is None:
            raise ValueError, files

        self.archive_purpose = extra_args.get('archive_purpose')
        self.distribution = extra_args['distribution']
        self.suite = extra_args['suite']
        self.component = extra_args['ogrecomponent']
        self.arch_indep = extra_args.get('arch_indep', False)
        self.build_debug_symbols = extra_args.get('build_debug_symbols', False)

        super(BinaryPackageBuildManager, self).initiate(
            files, chroot, extra_args)

    def doRunBuild(self):
        """Run the sbuild process to build the package."""
        currently_building_path = os.path.join(
            self.chroot_path, 'CurrentlyBuilding')
        currently_building_contents = (
            'Package: %s\n'
            'Component: %s\n'
            'Suite: %s\n'
            'Purpose: %s\n'
            % (self._dscfile.split('_')[0], self.component, self.suite,
               self.archive_purpose))
        if self.build_debug_symbols:
            currently_building_contents += 'Build-Debug-Symbols: yes\n'
        currently_building = open(currently_building_path, 'w')
        currently_building.write(currently_building_contents)
        currently_building.close()

        args = ["sbuild-package", self._buildid, self.arch_tag]
        args.append(self.suite)
        args.extend(["-c", "chroot:autobuild"])
        args.append("--arch=" + self.arch_tag)
        args.append("--dist=" + self.suite)
        args.append("--purge=never")
        args.append("--nolog")
        if self.arch_indep:
            args.append("-A")
        args.append(self._dscfile)
        self.runSubProcess(self._sbuildpath, args)

    def iterate_SBUILD(self, success):
        """Finished the sbuild run."""
        if success != SBuildExitCodes.OK:
            log_patterns = []
            stop_patterns = [["^Toolchain package versions:", re.M]]

            # We don't distinguish attempted and failed.
            if success == SBuildExitCodes.ATTEMPTED:
                success = SBuildExitCodes.FAILED

            if success == SBuildExitCodes.GIVENBACK:
                for rx in BuildLogRegexes.GIVENBACK:
                    log_patterns.append([rx, re.M])
                # XXX: Check if it has the right Fail-Stage
                if True:
                    for rx in BuildLogRegexes.DEPFAIL:
                        log_patterns.append([rx, re.M])

            missing_dep = None
            if log_patterns:
                rx, mo = self.searchLogContents(log_patterns, stop_patterns)
                if mo is None:
                    # It was givenback, but we can't see a valid reason.
                    # Assume it failed.
                    success = SBuildExitCodes.FAILED
                elif rx in BuildLogRegexes.DEPFAIL:
                    # A depwait match forces depwait.
                    missing_dep = mo.expand(BuildLogRegexes.DEPFAIL[rx])
                else:
                    # Otherwise it was a givenback pattern, so leave it
                    # in givenback.
                    pass

            if not self.alreadyfailed:
                if missing_dep is not None:
                    print("Returning build status: DEPFAIL")
                    print("Dependencies: " + missing_dep)
                    self._slave.depFail(missing_dep)
                elif success == SBuildExitCodes.GIVENBACK:
                    print("Returning build status: GIVENBACK")
                    self._slave.giveBack()
                elif success == SBuildExitCodes.FAILED:
                    print("Returning build status: PACKAGEFAIL")
                    self._slave.buildFail()
                elif success >= SBuildExitCodes.BUILDERFAIL:
                    # anything else is assumed to be a buildd failure
                    print("Returning build status: BUILDERFAIL")
                    self._slave.builderFail()
                self.alreadyfailed = True
            self.doReapProcesses(self._state)
        else:
            print("Returning build status: OK")
            try:
                self.gatherResults()
            except Exception, e:
                self._slave.log("Failed to gather results: %s" % e)
                self._slave.buildFail()
                self.alreadyfailed = True
            self.doReapProcesses(self._state)

    def iterateReap_SBUILD(self, success):
        """Finished reaping after sbuild run."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()
