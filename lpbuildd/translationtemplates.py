# Copyright 2010-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    )
from lpbuildd.target.generate_translation_templates import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
    )


class TranslationTemplatesBuildState(DebianBuildState):
    GENERATE = "GENERATE"


class TranslationTemplatesBuildManager(DebianBuildManager):
    """Generate translation templates from branch.

    This is the implementation of `TranslationTemplatesBuildJob`.  The
    latter runs on the master server; TranslationTemplatesBuildManager
    runs on the builder.
    """

    initial_build_state = TranslationTemplatesBuildState.GENERATE

    def __init__(self, builder, buildid):
        super(TranslationTemplatesBuildManager, self).__init__(
            builder, buildid)
        self._resultname = builder._config.get(
            "translationtemplatesmanager", "resultarchive")

    def initiate(self, files, chroot, extra_args):
        """See `BuildManager`."""
        self._branch_url = extra_args['branch_url']

        super(TranslationTemplatesBuildManager, self).initiate(
            files, chroot, extra_args)

    def doGenerate(self):
        """Generate templates."""
        self.runTargetSubProcess(
            "generate-translation-templates",
            self._branch_url, self._resultname)

    # Satisfy DebianPackageManager's needs without having a misleading
    # method name here.
    doRunBuild = doGenerate

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache."""
        # The file is inside the target, in the home directory of the buildd
        # user. Should be safe to assume the home dirs are named identically.
        path = os.path.join(self.home, self._resultname)
        if self.backend.path_exists(path):
            self.addWaitingFileFromBackend(path)

    def iterate_GENERATE(self, retcode):
        """Template generation finished."""
        if retcode == 0:
            # It worked! Now let's bring in the harvest.
            return self.deferGatherResults()
        else:
            if not self.alreadyfailed:
                if retcode == RETCODE_FAILURE_INSTALL:
                    self._builder.chrootFail()
                elif retcode == RETCODE_FAILURE_BUILD:
                    self._builder.buildFail()
                else:
                    self._builder.builderFail()
                self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_GENERATE(self, success):
        """Finished reaping after template generation."""
        self._state = TranslationTemplatesBuildState.UMOUNT
        self.doUnmounting()
