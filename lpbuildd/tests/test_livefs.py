# Copyright 2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import shutil
import tempfile

from testtools import TestCase

from lpbuildd.livefs import (
    LiveFilesystemBuildManager,
    LiveFilesystemBuildState,
    )
from lpbuildd.tests.fakeslave import FakeSlave


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
        self.working_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.working_dir))
        slave_dir = os.path.join(self.working_dir, "slave")
        home_dir = os.path.join(self.working_dir, "home")
        for dir in (slave_dir, home_dir):
            os.mkdir(dir)
        self.slave = FakeSlave(slave_dir)
        self.buildid = "123"
        self.buildmanager = MockBuildManager(self.slave, self.buildid)
        self.buildmanager.home = home_dir
        self.buildmanager._cachepath = self.slave._cachepath
        self.build_dir = os.path.join(
            home_dir, "build-%s" % self.buildid, "chroot-autobuild", "build")

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
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_LIVEFS.
        self.buildmanager._state = LiveFilesystemBuildState.UPDATE

        # BUILD_LIVEFS: Run the slave's payload to build the live filesystem.
        self.buildmanager.iterate(0)
        self.assertEqual(
            LiveFilesystemBuildState.BUILD_LIVEFS, self.getState())
        expected_command = [
            "sharepath/slavebin/buildlivefs", "buildlivefs", "--build-id",
            self.buildid, "--arch", "i386", "--project", "ubuntu",
            "--series", "saucy",
            ]
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("chrootFail"))

    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        log = open(log_path, "w")
        log.write("I am a build log.")
        log.close()

        os.makedirs(self.build_dir)
        manifest_path = os.path.join(self.build_dir, "livecd.ubuntu.manifest")
        manifest = open(manifest_path, "w")
        manifest.write("I am a manifest file.")
        manifest.close()

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/slavebin/scan-for-processes", "scan-for-processes",
            self.buildid,
            ]
        self.assertEqual(
            LiveFilesystemBuildState.BUILD_LIVEFS, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertEqual(
            [((manifest_path,), {})], self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/slavebin/umount-chroot", "umount-chroot",
            "--backend=chroot", "--series=saucy", "--arch=i386",
            self.buildid,
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
        log = open(log_path, "w")
        log.write("I am a build log.")
        log.close()

        os.makedirs(self.build_dir)
        target_path = os.path.join(
            self.build_dir, "livecd.ubuntu.kernel-generic")
        target = open(target_path, "w")
        target.write("I am a kernel.")
        target.close()
        link_path = os.path.join(self.build_dir, "livecd.ubuntu.kernel")
        os.symlink("livecd.ubuntu.kernel-generic", link_path)

        self.buildmanager.iterate(0)
        self.assertEqual(
            [((target_path,), {})], self.slave.addWaitingFile.calls)
