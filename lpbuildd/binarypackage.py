# Copyright 2009, 2010 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import

from collections import defaultdict
import os
import re

import apt_pkg
from debian.deb822 import (
    Dsc,
    PkgRelation,
    )
from debian.debian_support import Version

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    )


class SBuildExitCodes:
    """SBUILD process result codes."""
    OK = 0
    FAILED = 1
    ATTEMPTED = 2
    GIVENBACK = 3
    BUILDERFAIL = 4


APT_MISSING_DEP_PATTERNS = [
    'but [^ ]* is to be installed',
    'but [^ ]* is installed',
    'but it is not installable',
    'but it is a virtual package',
    ]


APT_DUBIOUS_DEP_PATTERNS = [
    'but it is not installed',
    'but it is not going to be installed',
    ]


class BuildLogRegexes:
    """Build log regexes for performing actions based on regexes, and extracting dependencies for auto dep-waits"""
    GIVENBACK = [
        ("^E: There are problems and -y was used without --force-yes"),
        ]
    DEPFAIL = {
        'The following packages have unmet dependencies:\n'
        '.*: Depends: (?P<p>[^ ]*( \([^)]*\))?) (%s)\n'
        % '|'.join(APT_MISSING_DEP_PATTERNS): "\g<p>",
        }
    MAYBEDEPFAIL = [
        'The following packages have unmet dependencies:\n'
        '.*: Depends: [^ ]*( \([^)]*\))? (%s)\n'
        % '|'.join(APT_DUBIOUS_DEP_PATTERNS),
        ]


class BinaryPackageBuildState(DebianBuildState):
    SBUILD = "SBUILD"


class BinaryPackageBuildManager(DebianBuildManager):
    """Handle buildd building for a debian style binary package build"""

    initial_build_state = BinaryPackageBuildState.SBUILD

    def __init__(self, slave, buildid, **kwargs):
        DebianBuildManager.__init__(self, slave, buildid, **kwargs)
        self._sbuildpath = os.path.join(self._slavebin, "sbuild-package")

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
        with open(currently_building_path, 'w') as currently_building:
            currently_building.write(currently_building_contents)

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

    def getAvailablePackages(self):
        """Return the available binary packages in the chroot.

        :return: A dictionary mapping package names to a set of the
            available versions of each package.
        """
        available = defaultdict(set)
        apt_lists = os.path.join(
            self.chroot_path, "var", "lib", "apt", "lists")
        for name in sorted(os.listdir(apt_lists)):
            if name.endswith("_Packages"):
                path = os.path.join(apt_lists, name)
                with open(path, "rb") as packages_file:
                    for section in apt_pkg.TagFile(packages_file):
                        available[section["package"]].add(section["version"])
                        if "provides" in section:
                            provides = apt_pkg.parse_depends(
                                section["provides"])
                            for provide in provides:
                                # Disjunctions are currently undefined here.
                                if len(provide) > 1:
                                    continue
                                # Virtual packages may only provide an exact
                                # version or none.
                                if provide[0][1] and provide[0][2] != "=":
                                    continue
                                available[provide[0][0]].add(
                                    provide[0][1] if provide[0][1] else None)
        return available

    def getBuildDepends(self, dscpath, arch_indep):
        """Get the build-dependencies of a source package.

        :param dscpath: The path of a .dsc file.
        :param arch_indep: True iff we were asked to build the
            architecture-independent parts of this source package.
        :return: The build-dependencies, in the form returned by
            `debian.deb822.PkgRelation.parse_relations`.
        """
        deps = []
        with open(dscpath, "rb") as dscfile:
            dsc = Dsc(dscfile)
            fields = ["Build-Depends"]
            if arch_indep:
                fields.append("Build-Depends-Indep")
            for field in fields:
                if field in dsc:
                    deps.extend(PkgRelation.parse_relations(dsc[field]))
        return deps

    def relationMatches(self, dep, available):
        """Return True iff a dependency matches an available package.

        :param dep: A dictionary with at least a "name" key, perhaps also a
            "version" key, and optionally other keys, of the kind returned
            in a list of lists by
            `debian.deb822.PkgRelation.parse_relations`.
        :param available: A dictionary mapping package names to a list of
            the available versions of each package.
        """
        if dep["name"] not in available:
            return False
        if dep.get("version") is None:
            return True
        dep_version = dep.get("version")
        operator_map = {
            "<<": (lambda a, b: a < b),
            "<=": (lambda a, b: a <= b),
            "=": (lambda a, b: a == b),
            ">=": (lambda a, b: a >= b),
            ">>": (lambda a, b: a > b),
            }
        operator = operator_map[dep_version[0]]
        want_version = dep_version[1]
        for version in available[dep["name"]]:
            if (version is not None and
                    operator(Version(version), want_version)):
                return True
        return False

    def analyseDepWait(self, deps, avail):
        """Work out the correct dep-wait for a failed build, if any.

        We only consider direct build-dependencies, because indirect ones
        can't easily be turned into an accurate dep-wait: they might be
        resolved either by an intermediate package changing or by the
        missing dependency becoming available.  We err on the side of
        failing a build rather than setting a dep-wait if it's possible that
        the dep-wait might be incorrect.  Any exception raised during the
        analysis causes the build to be failed.

        :param deps: The source package's build-dependencies, in the form
            returned by `debian.deb822.PkgRelation.parse_relations`.
        :param avail: A dictionary mapping package names to a set of the
            available versions of each package.
        :return: A dependency relation string representing the packages that
            need to become available before this build can proceed, or None
            if the build should be failed instead.
        """
        try:
            unsat_deps = []
            for or_dep in deps:
                if not any(self.relationMatches(dep, avail) for dep in or_dep):
                    unsat_deps.append(or_dep)
            if unsat_deps:
                return PkgRelation.str(unsat_deps)
        except Exception as e:
            print("Failed to analyse dep-wait: %s" % e)
        return None

    def iterate_SBUILD(self, success):
        """Finished the sbuild run."""
        if success == SBuildExitCodes.OK:
            print("Returning build status: OK")
            try:
                self.gatherResults()
            except Exception, e:
                self._slave.log("Failed to gather results: %s" % e)
                self._slave.buildFail()
                self.alreadyfailed = True
            self.doReapProcesses(self._state)
            return

        log_patterns = []
        stop_patterns = [["^Toolchain package versions:", re.M]]

        # We don't distinguish attempted and failed.
        if success == SBuildExitCodes.ATTEMPTED:
            success = SBuildExitCodes.FAILED

        if success == SBuildExitCodes.GIVENBACK:
            for rx in BuildLogRegexes.GIVENBACK:
                log_patterns.append([rx, re.M])
            # Check the last 4KiB for the Fail-Stage. If it failed
            # during install-deps, search for the missing dependency
            # string.
            with open(os.path.join(self._cachepath, "buildlog")) as log:
                try:
                    log.seek(-4096, os.SEEK_END)
                except IOError:
                    pass
                tail = log.read(4096)
            if re.search("^Fail-Stage: install-deps$", tail, re.M):
                for rx in BuildLogRegexes.DEPFAIL:
                    log_patterns.append([rx, re.M])
                for rx in BuildLogRegexes.MAYBEDEPFAIL:
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
            elif rx in BuildLogRegexes.MAYBEDEPFAIL:
                # These matches need further analysis.
                dscpath = os.path.join(self.home, self._dscfile)
                missing_dep = self.analyseDepWait(
                    self.getBuildDepends(dscpath, self.arch_indep),
                    self.getAvailablePackages())
                if missing_dep is None:
                    success = SBuildExitCodes.FAILED
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

    def iterateReap_SBUILD(self, success):
        """Finished reaping after sbuild run."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()
