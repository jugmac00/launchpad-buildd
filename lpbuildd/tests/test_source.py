import base64
import os

import responses
from fixtures import EnvironmentVariable, TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import defer

from lpbuildd.source import SourceBuildManager, SourceBuildState
from lpbuildd.tests.fakebuilder import FakeBuilder
from lpbuildd.tests.matchers import HasWaitingFiles


class MockBuildManager(SourceBuildManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []

    def runSubProcess(self, path, command, iterate=None, env=None):
        self.commands.append([path] + command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        return 0
    

class TestSourceBuildManagerIteration(TestCase):
    """Run SourceBuildManager through its iteration steps."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def setUp(self):
        super().setUp()
        self.working_dir = self.useFixture(TempDir()).path
        builder_dir = os.path.join(self.working_dir, "builder")
        home_dir = os.path.join(self.working_dir, "home")
        for dir in (builder_dir, home_dir):
            os.mkdir(dir)
        self.useFixture(EnvironmentVariable("HOME", home_dir))
        self.builder = FakeBuilder(builder_dir)
        self.buildid = "123"
        self.buildmanager = MockBuildManager(self.builder, self.buildid)
        self.buildmanager._cachepath = self.builder._cachepath

    def getState(self):
        """Retrieve build manager's state."""
        return self.buildmanager._state

    @defer.inlineCallbacks
    def startBuild(self, args=None, options=None):
        # The build manager's iterate() kicks off the consecutive states
        # after INIT.
        extra_args = {
            "series": "xenial",
            "arch_tag": "i386",
            "name": "test-source",
        }
        if args is not None:
            extra_args.update(args)
        original_backend_name = self.buildmanager.backend_name
        self.buildmanager.backend_name = "fake"
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)
        self.buildmanager.backend_name = original_backend_name

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_SOURCE.
        self.buildmanager._state = SourceBuildState.UPDATE

        # BUILD_SOURCE: Run the builder's payload to build the source.
        yield self.buildmanager.iterate(0)
        self.assertEqual(SourceBuildState.BUILD_SOURCE, self.getState())
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "build-source",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]
        if options is not None:
            expected_command.extend(options)
        expected_command.append("test-source")
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("chrootFail"))

    def test_status(self):
        # The build manager returns saved status information on request.
        self.assertEqual({}, self.buildmanager.status())
        status_path = os.path.join(
            self.working_dir, "home", "build-%s" % self.buildid, "status"
        )
        os.makedirs(os.path.dirname(status_path))
        with open(status_path, "w") as status_file:
            status_file.write('{"revision_id": "foo"}')
        self.assertEqual({"revision_id": "foo"}, self.buildmanager.status())

    @defer.inlineCallbacks
    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/source",
            "git_path": "master",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/source",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/home/buildd/test-source/test-source_0_all.tar.xz", b"I am sourceing."
        )

        # After building the package, reap processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "scan-for-processes",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]

        self.assertEqual(SourceBuildState.BUILD_SOURCE, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-source_0_all.tar.xz": b"I am sourceing.",
                }
            ),
        )

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "umount-chroot",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]
        self.assertEqual(SourceBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_build_path(self):
        # The build manager iterates a build using build_path from start to
        # finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/source",
            "git_path": "master",
            "build_path": "source",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/source",
            "--git-path",
            "master",
            "--build-path",
            "source",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/home/buildd/test-source/source/test-source_0_all.tar.xz",
            b"I am sourceing.",
        )

        # After building the package, reap processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "scan-for-processes",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]

        self.assertEqual(SourceBuildState.BUILD_SOURCE, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-source_0_all.tar.xz": b"I am sourceing.",
                }
            ),
        )

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "umount-chroot",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]
        self.assertEqual(SourceBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
