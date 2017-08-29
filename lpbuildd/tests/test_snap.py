# Copyright 2015-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from testtools import TestCase

from lpbuildd.snap import (
    SnapBuildManager,
    SnapBuildState,
    )
from lpbuildd.tests.fakeslave import FakeSlave
from lpbuildd.tests.matchers import HasWaitingFiles


class MockBuildManager(SnapBuildManager):
    def __init__(self, *args, **kwargs):
        super(MockBuildManager, self).__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []

    def runSubProcess(self, path, command, iterate=None, env=None):
        self.commands.append([path] + command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        return 0


class TestSnapBuildManagerIteration(TestCase):
    """Run SnapBuildManager through its iteration steps."""
    def setUp(self):
        super(TestSnapBuildManagerIteration, self).setUp()
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
            "series": "xenial",
            "arch_tag": "i386",
            "name": "test-snap",
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            }
        original_backend_name = self.buildmanager.backend_name
        self.buildmanager.backend_name = "fake"
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)
        self.buildmanager.backend_name = original_backend_name

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_SNAP.
        self.buildmanager._state = SnapBuildState.UPDATE

        # BUILD_SNAP: Run the slave's payload to build the snap package.
        self.buildmanager.iterate(0)
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "buildsnap",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            "--git-repository", "https://git.launchpad.dev/~example/+git/snap",
            "--git-path", "master",
            "test-snap",
            ]
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("chrootFail"))

    def test_status(self):
        # The build manager returns saved status information on request.
        self.assertEqual({}, self.buildmanager.status())
        status_path = os.path.join(
            self.working_dir, "home", "build-%s" % self.buildid, "status")
        os.makedirs(os.path.dirname(status_path))
        with open(status_path, "w") as status_file:
            status_file.write('{"revision_id": "dummy"}')
        self.assertEqual({"revision_id": "dummy"}, self.buildmanager.status())

    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package.")

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "scan-for-processes",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertThat(self.slave, HasWaitingFiles.byEquality({
            "test-snap_0_all.snap": b"I am a snap package.",
            }))

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "umount-chroot",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))

    def test_iterate_with_manifest(self):
        # The build manager iterates a build that uploads a manifest from
        # start to finish.
        self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package.")
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.manifest", b"I am a manifest.")

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "scan-for-processes",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertThat(self.slave, HasWaitingFiles.byEquality({
            "test-snap_0_all.manifest": b"I am a manifest.",
            "test-snap_0_all.snap": b"I am a snap package.",
            }))

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/slavebin/in-target", "in-target",
            "umount-chroot",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
