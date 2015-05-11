# Copyright 2013 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import tempfile

import os
import shutil
from testtools import TestCase

from twisted.internet.task import Clock

from lpbuildd.binarypackage import (
    BinaryPackageBuildManager,
    BinaryPackageBuildState,
    )
from lpbuildd.tests.fakeslave import (
    FakeMethod,
    FakeSlave,
    )


class MockTransport:
    def __init__(self):
        self.loseConnection = FakeMethod()
        self.signalProcess = FakeMethod()


class MockSubprocess:
    def __init__(self, path):
        self.path = path
        self.transport = MockTransport()


class MockBuildManager(BinaryPackageBuildManager):
    def __init__(self, *args, **kwargs):
        super(MockBuildManager, self).__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []

    def runSubProcess(self, path, command, iterate=None):
        self.commands.append([path]+command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        self._subprocess = MockSubprocess(path)
        return 0


def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


class TestBinaryPackageBuildManagerIteration(TestCase):
    """Run BinaryPackageBuildManager through its iteration steps."""
    def setUp(self):
        super(TestBinaryPackageBuildManagerIteration, self).setUp()
        self.working_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.working_dir))
        slave_dir = os.path.join(self.working_dir, 'slave')
        home_dir = os.path.join(self.working_dir, 'home')
        for dir in (slave_dir, home_dir):
            os.mkdir(dir)
        self.slave = FakeSlave(slave_dir)
        self.buildid = '123'
        self.clock = Clock()
        self.buildmanager = MockBuildManager(
            self.slave, self.buildid, reactor=self.clock)
        self.buildmanager.home = home_dir
        self.buildmanager._cachepath = self.slave._cachepath
        self.chrootdir = os.path.join(
            home_dir, 'build-%s' % self.buildid, 'chroot-autobuild')

    def getState(self):
        """Retrieve build manager's state."""
        return self.buildmanager._state

    def startBuild(self):
        # The build manager's iterate() kicks off the consecutive states
        # after INIT.
        self.buildmanager.initiate(
            {'foo_1.dsc': ''}, 'chroot.tar.gz',
            {'distribution': 'ubuntu', 'suite': 'warty',
             'ogrecomponent': 'main'})

        # Skip DebianBuildManager states to the state directly before
        # SBUILD.
        self.buildmanager._state = BinaryPackageBuildState.UPDATE

        # SBUILD: Build the package.
        self.buildmanager.iterate(0)
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            [
            'sharepath/slavebin/sbuild-package', 'sbuild-package',
            self.buildid, 'i386', 'warty', 'sbuildargs', '--archive=ubuntu',
            '--dist=warty', '--architecture=i386', '--comp=main', 'foo_1.dsc',
            ], True)
        self.assertFalse(self.slave.wasCalled('chrootFail'))

    def assertState(self, state, command, final):
        self.assertEqual(state, self.getState())
        self.assertEqual(command, self.buildmanager.commands[-1])
        if final:
            self.assertEqual(
                self.buildmanager.iterate, self.buildmanager.iterators[-1])
        else:
            self.assertNotEqual(
                self.buildmanager.iterate, self.buildmanager.iterators[-1])

    def assertScansSanely(self, exit_code):
        # After building the package, reap processes.
        self.buildmanager.iterate(exit_code)
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)

    def assertUnmountsSanely(self):
        self.buildmanager.iterateReap(self.getState(), 0)
        self.assertState(
            BinaryPackageBuildState.UMOUNT,
            ['sharepath/slavebin/umount-chroot', 'umount-chroot',
             self.buildid], True)

    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        self.startBuild()

        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "I am a build log.")
        changes_path = os.path.join(
            self.buildmanager.home, 'build-%s' % self.buildid,
            'foo_1_i386.changes')
        write_file(changes_path, "I am a changes file.")

        # After building the package, reap processes.
        self.assertScansSanely(0)
        self.assertFalse(self.slave.wasCalled('buildFail'))
        self.assertEqual(
            [((changes_path,), {})], self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_abort_sbuild(self):
        # Aborting sbuild kills processes in the chroot.
        self.startBuild()

        # Send an abort command.  The build manager reaps processes.
        self.buildmanager.abort()
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)
        self.assertFalse(self.slave.wasCalled('buildFail'))

        # If reaping completes successfully, the build manager returns
        # control to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_abort_sbuild_fail(self):
        # If killing processes in the chroot hangs, the build manager does
        # its best to clean up and fails the builder.
        self.startBuild()
        sbuild_subprocess = self.buildmanager._subprocess

        # Send an abort command.  The build manager reaps processes.
        self.buildmanager.abort()
        self.assertState(
            BinaryPackageBuildState.SBUILD,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)
        self.assertFalse(self.slave.wasCalled('builderFail'))
        reap_subprocess = self.buildmanager._subprocess

        # If reaping fails, the builder is failed, sbuild is killed, and the
        # reaper is disconnected.
        self.clock.advance(120)
        self.assertTrue(self.slave.wasCalled('builderFail'))
        self.assertEqual(
            [(('KILL',), {})], sbuild_subprocess.transport.signalProcess.calls)
        self.assertNotEqual(
            [], sbuild_subprocess.transport.loseConnection.calls)
        self.assertNotEqual([], reap_subprocess.transport.loseConnection.calls)

        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "I am a build log.")

        # When sbuild exits, it does not reap processes again, but proceeds
        # directly to UMOUNT.
        self.buildmanager.iterate(128 + 9)  # SIGKILL
        self.assertState(
            BinaryPackageBuildState.UMOUNT,
            ['sharepath/slavebin/umount-chroot', 'umount-chroot',
             self.buildid], True)

    def test_abort_between_subprocesses(self):
        # If a build is aborted between subprocesses, the build manager
        # pretends that it was terminated by a signal.
        self.buildmanager.initiate(
            {'foo_1.dsc': ''}, 'chroot.tar.gz',
            {'distribution': 'ubuntu', 'suite': 'warty',
             'ogrecomponent': 'main'})

        self.buildmanager.abort()
        self.assertState(
            BinaryPackageBuildState.INIT,
            ['sharepath/slavebin/scan-for-processes', 'scan-for-processes',
             self.buildid], False)

        self.buildmanager.iterate(0)
        self.assertState(
            BinaryPackageBuildState.CLEANUP,
            ['sharepath/slavebin/remove-build', 'remove-build', self.buildid],
            True)
        self.assertFalse(self.slave.wasCalled('builderFail'))

    def test_missing_changes(self):
        # The build manager recovers if the expected .changes file does not
        # exist, and considers it a package build failure.
        self.startBuild()
        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "I am a build log.")
        build_dir = os.path.join(
            self.buildmanager.home, 'build-%s' % self.buildid)
        write_file(
            os.path.join(build_dir, 'foo_2_i386.changes'),
            "I am a changes file.")

        # After building the package, reap processes.
        self.assertScansSanely(0)
        self.assertTrue(self.slave.wasCalled('buildFail'))
        self.assertEqual(
            [((os.path.join(build_dir, 'foo_1_i386.changes'),), {})],
            self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertTrue(self.slave.wasCalled('buildFail'))

    def test_detects_depfail(self):
        # The build manager detects dependency installation failures.
        self.startBuild()
        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "E: Unable to locate package nonexistent\n")

        # After building the package, reap processes.
        self.assertScansSanely(1)
        self.assertFalse(self.slave.wasCalled('buildFail'))
        self.assertEqual([(("nonexistent",), {})], self.slave.depFail.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.assertUnmountsSanely()
        self.assertFalse(self.slave.wasCalled('buildFail'))

    def test_depfail_with_unknown_error_converted_to_packagefail(self):
        # The build manager converts a DEPFAIL to a PACKAGEFAIL if the
        # missing dependency can't be determined from the log.
        self.startBuild()
        write_file(
            os.path.join(self.buildmanager._cachepath, 'buildlog'),
            "E: Everything is broken.\n")

        self.assertScansSanely(1)
        self.assertTrue(self.slave.wasCalled('buildFail'))
        self.assertFalse(self.slave.wasCalled('depFail'))
