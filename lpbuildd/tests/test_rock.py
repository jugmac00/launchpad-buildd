import os

from fixtures import EnvironmentVariable, TempDir
from testtools import TestCase
from testtools.twistedsupport import AsynchronousDeferredRunTest
from twisted.internet import defer

from lpbuildd.rock import RockBuildManager, RockBuildState
from lpbuildd.tests.fakebuilder import FakeBuilder
from lpbuildd.tests.matchers import HasWaitingFiles


class MockBuildManager(RockBuildManager):
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


class TestRockBuildManagerIteration(TestCase):
    """Run RockBuildManager through its iteration steps."""

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
            "name": "test-rock",
        }
        if args is not None:
            extra_args.update(args)
        original_backend_name = self.buildmanager.backend_name
        self.buildmanager.backend_name = "fake"
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)
        self.buildmanager.backend_name = original_backend_name

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_ROCK.
        self.buildmanager._state = RockBuildState.UPDATE

        # BUILD_ROCK: Run the builder's payload to build the rock.
        yield self.buildmanager.iterate(0)
        self.assertEqual(RockBuildState.BUILD_ROCK, self.getState())
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "build-rock",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]
        if options is not None:
            expected_command.extend(options)
        expected_command.append("test-rock")
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
            "git_repository": "https://git.launchpad.dev/~example/+git/rock",
            "git_path": "master",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/rock",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/home/buildd/test-rock/test-rock_0_all.rock", b"I am rocking."
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

        self.assertEqual(RockBuildState.BUILD_ROCK, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-rock_0_all.rock": b"I am rocking.",
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
        self.assertEqual(RockBuildState.UMOUNT, self.getState())
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
            "git_repository": "https://git.launchpad.dev/~example/+git/rock",
            "git_path": "master",
            "build_path": "rock",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/rock",
            "--git-path",
            "master",
            "--build-path",
            "rock",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/home/buildd/test-rock/rock/test-rock_0_all.rock",
            b"I am rocking.",
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

        self.assertEqual(RockBuildState.BUILD_ROCK, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-rock_0_all.rock": b"I am rocking.",
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
        self.assertEqual(RockBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_use_fetch_service(self):
        # The build manager can be told to use the fetch service as its proxy.
        # This requires also a ca certificate passed in via secrets.
        args = {
            "use_fetch_service": True,
            "secrets": {"fetch_service_mitm_certificate": "content_of_cert"},
        }
        expected_options = [
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            "content_of_cert",
        ]
        yield self.startBuild(args, expected_options)

    @defer.inlineCallbacks
    def test_iterate_launchpad_url_and_instance(self):
        # The builder should be aware of the launchpad context.
        args = {
            "launchpad_instance": "devel",
            "launchpad_server_url": "launchpad.test",
        }
        expected_options = [
            "--launchpad-instance",
            "devel",
            "--launchpad-server-url",
            "launchpad.test",
        ]
        yield self.startBuild(args, expected_options)
