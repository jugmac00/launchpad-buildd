# Copyright 2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from testtools import TestCase

from lpbuildd.livefs import (
    LiveFilesystemBuildManager,
    LiveFilesystemBuildState,
    )
from lpbuildd.tests.fakeslave import FakeSlave
from lpbuildd.tests.matchers import HasWaitingFiles


class MockBuildManager(LiveFilesystemBuildManager):
    def __init__(self, *args, **kwargs):
        super(MockBuildManager, self).__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []

    def runSubProcess(self, path, command, iterate=None):
        self.commands.append([path] + command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        return 0


class TestLiveFilesystemBuildManagerIteration(TestCase):
    """Run LiveFilesystemBuildManager through its iteration steps."""
    def setUp(self):
        super(TestLiveFilesystemBuildManagerIteration, self).setUp()
        self.working_dir = self.useFixture(TempDir()).path
        slave_dir = os.path.join(self.working_dir, "slave")
        home_dir = os.path.join(self.working_dir, "home")
        for dir in (slave_dir, home_dir):
            os.mkdir(dir)
        self.useFixture(EnvironmentVariable("HOME", home_dir))
        self.slave = FakeSlave(slave_dir)
        self.buildid = "123"
        self.buildmanager = MockBuildManager(self.slave, self.buildid)
        self.buildmanager._cachepath = self.slave._cachepath

    def getState(self):
        """Retrieve build manager's state."""
        return self.buildmanager._state

    def startBuild(self):
        # The build manager's iterate() kicks off the consecutive states
        # after INIT.
        extra_args = {
            "project": "ubuntu",
            "series": "saucy",
            "pocket": "release",
            "arch_tag": "i386",
            }
        original_backend_name = self.buildmanager.backend_name
        self.buildmanager.backend_name = "fake"
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)
        self.buildmanager.backend_name = original_backend_name

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_LIVEFS.
        self.buildmanager._state = LiveFilesystemBuildState.UPDATE

        # BUILD_LIVEFS: Run the slave's payload to build the live filesystem.
        self.buildmanager.iterate(0)
        self.assertEqual(
            LiveFilesystemBuildState.BUILD_LIVEFS, self.getState())
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "buildlivefs",
            "--backend=lxd", "--series=saucy", "--arch=i386", self.buildid,
            "--project", "ubuntu",
            ]
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("chrootFail"))

    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/livecd.ubuntu.manifest", b"I am a manifest file.")

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "scan-for-processes",
            "--backend=lxd", "--series=saucy", "--arch=i386", self.buildid,
            ]
        self.assertEqual(
            LiveFilesystemBuildState.BUILD_LIVEFS, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertThat(self.slave, HasWaitingFiles.byEquality({
            "livecd.ubuntu.manifest": b"I am a manifest file.",
            }))

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "umount-chroot",
            "--backend=lxd", "--series=saucy", "--arch=i386", self.buildid,
            ]
        self.assertEqual(LiveFilesystemBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))

    def test_omits_symlinks(self):
        # Symlinks in the build output are not included in gathered results.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/livecd.ubuntu.kernel-generic", b"I am a kernel.")
        self.buildmanager.backend.add_link(
            "/build/livecd.ubuntu.kernel", "livefs.ubuntu.kernel-generic")

        self.buildmanager.iterate(0)
        self.assertThat(self.slave, HasWaitingFiles.byEquality({
            "livecd.ubuntu.kernel-generic": b"I am a kernel.",
            }))
