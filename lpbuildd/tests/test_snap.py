# Copyright 2015-2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64
import os

import responses
from fixtures import EnvironmentVariable, TempDir
from testtools import TestCase
from testtools.content import text_content
from testtools.twistedsupport import AsynchronousDeferredRunTest
from twisted.internet import defer, reactor, utils
from twisted.web import http, proxy, resource, server, static

from lpbuildd.proxy import BuilderProxyFactory
from lpbuildd.snap import SnapBuildManager, SnapBuildState
from lpbuildd.tests.fakebuilder import FakeBuilder
from lpbuildd.tests.matchers import HasWaitingFiles


class MockBuildManager(SnapBuildManager):
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


class TestSnapBuildManagerIteration(TestCase):
    """Run SnapBuildManager through its iteration steps."""

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
            "name": "test-snap",
        }
        if args is not None:
            extra_args.update(args)
        original_backend_name = self.buildmanager.backend_name
        self.buildmanager.backend_name = "fake"
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)
        self.buildmanager.backend_name = original_backend_name

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_SNAP.
        self.buildmanager._state = SnapBuildState.UPDATE

        # BUILD_SNAP: Run the builder's payload to build the snap package.
        yield self.buildmanager.iterate(0)
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "buildsnap",
            "--backend=lxd",
            "--series=xenial",
            "--arch=i386",
            self.buildid,
        ]
        if options is not None:
            expected_command.extend(options)
        expected_command.append("test-snap")
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
            status_file.write('{"revision_id": "dummy"}')
        self.assertEqual({"revision_id": "dummy"}, self.buildmanager.status())

    @defer.inlineCallbacks
    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        args = {
            "build_request_id": 13,
            "build_request_timestamp": "2018-04-13T14:50:02Z",
            "build_url": "https://launchpad.example/build",
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
        }
        expected_options = [
            "--build-request-id",
            "13",
            "--build-request-timestamp",
            "2018-04-13T14:50:02Z",
            "--build-url",
            "https://launchpad.example/build",
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.snap": b"I am a snap package.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_manifest(self):
        # The build manager iterates a build that uploads a manifest from
        # start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
        )
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.manifest", b"I am a manifest."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.manifest": b"I am a manifest.",
                    "test-snap_0_all.snap": b"I am a snap package.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_components(self):
        """Test building snap components

        The build manager iterates a build that uploads components from
        start to finish. We make sure that components exist and are correctly
        added to the build results.
        """

        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
        )
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap+somecomponent_0.comp",
            b"I am a component.",
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap+somecomponent_0.comp": b"I am a component.",
                    "test-snap_0_all.snap": b"I am a snap package.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_debug(self):
        # The build manager iterates a build that uploads debug symbols from
        # start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
        )
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.debug", b"I am debug symbols."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.debug": b"I am debug symbols.",
                    "test-snap_0_all.snap": b"I am a snap package.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_dpkg_yaml(self):
        # The build manager iterates a build that uploads dpkg.yaml from
        # start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
        )
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.manifest", b"I am a manifest."
        )
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.dpkg.yaml", b"I am a yaml file."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.manifest": b"I am a manifest.",
                    "test-snap_0_all.snap": b"I am a snap package.",
                    "test-snap_0_all.dpkg.yaml": b"I am a yaml file.",
                }
            ),
        )
        # Ensure we don't just gather any yaml file but exactly
        # the dpkg yaml.
        self.assertNotEqual(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.manifest": b"I am a manifest.",
                    "test-snap_0_all.snap": b"I am a snap package.",
                    "test-snap_0_all.snapcraft.yaml": b"I am a yaml file.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_channels(self):
        # The build manager iterates a build that specifies channels from
        # start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            "channels": {
                "core": "candidate",
                "core18": "beta",
                "snapcraft": "edge",
            },
        }
        expected_options = [
            "--channel",
            "core=candidate",
            "--channel",
            "core18=beta",
            "--channel",
            "snapcraft=edge",
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.snap": b"I am a snap package.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_build_source_tarball(self):
        # The build manager iterates a build that uploads a source tarball
        # from start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            "build_source_tarball": True,
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
            "--build-source-tarball",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
        )
        self.buildmanager.backend.add_file(
            "/build/test-snap.tar.gz", b"I am a source tarball."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.snap": b"I am a snap package.",
                    "test-snap.tar.gz": b"I am a source tarball.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_private(self):
        # The build manager iterates a private build from start to finish.
        args = {
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            "private": True,
        }
        expected_options = [
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
            "--private",
        ]
        yield self.startBuild(args, expected_options)

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package."
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
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "test-snap_0_all.snap": b"I am a snap package.",
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
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_snap_store_proxy(self):
        # The build manager can be told to use a snap store proxy.
        self.builder._config.set(
            "proxy", "snapstore", "http://snap-store-proxy.example/"
        )
        expected_options = [
            "--snap-store-proxy-url",
            "http://snap-store-proxy.example/",
        ]
        yield self.startBuild(options=expected_options)

    @defer.inlineCallbacks
    def test_iterate_target_architectures(self):
        args = {
            "build_request_id": 13,
            "build_request_timestamp": "2018-04-13T14:50:02Z",
            "build_url": "https://launchpad.example/build",
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            "target_architectures": ["i386", "amd64"],
        }
        expected_options = [
            "--build-request-id",
            "13",
            "--build-request-timestamp",
            "2018-04-13T14:50:02Z",
            "--build-url",
            "https://launchpad.example/build",
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
            "--target-arch",
            "i386",
            "--target-arch",
            "amd64",
        ]
        yield self.startBuild(args, expected_options)

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

    @defer.inlineCallbacks
    def test_iterate_disable_proxy_after_pull(self):
        self.builder._config.set("builder", "proxyport", "8222")
        args = {
            "disable_proxy_after_pull": True,
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            "proxy_url": "http://username:password@proxy.example/",
            "revocation_endpoint": (
                f"http://proxy-auth.example/tokens/{self.buildid}"
            ),
        }
        expected_options = [
            "--proxy-url",
            "http://localhost:8222/",
            "--revocation-endpoint",
            f"http://proxy-auth.example/tokens/{self.buildid}",
            "--upstream-proxy-url",
            "http://username:password@proxy.example/",
            "--disable-proxy-after-pull",
            "--git-repository",
            "https://git.launchpad.dev/~example/+git/snap",
            "--git-path",
            "master",
        ]
        try:
            yield self.startBuild(args, expected_options)
        finally:
            self.buildmanager.stopProxy()

    def getListenerURL(self, listener):
        port = listener.getHost().port
        return "http://localhost:%d/" % port

    def startFakeRemoteEndpoint(self):
        remote_endpoint = resource.Resource()
        remote_endpoint.putChild(b"x", static.Data(b"x" * 1024, "text/plain"))
        remote_endpoint.putChild(b"y", static.Data(b"y" * 65536, "text/plain"))
        remote_endpoint_listener = reactor.listenTCP(
            0, server.Site(remote_endpoint)
        )
        self.addCleanup(remote_endpoint_listener.stopListening)
        return remote_endpoint_listener

    def startFakeRemoteProxy(self):
        remote_proxy_factory = http.HTTPFactory()
        remote_proxy_factory.protocol = proxy.Proxy
        remote_proxy_listener = reactor.listenTCP(0, remote_proxy_factory)
        self.addCleanup(remote_proxy_listener.stopListening)
        return remote_proxy_listener

    def startLocalProxy(self, remote_url):
        proxy_factory = BuilderProxyFactory(
            self.buildmanager, remote_url, timeout=60
        )
        proxy_listener = reactor.listenTCP(0, proxy_factory)
        self.addCleanup(proxy_listener.stopListening)
        return proxy_listener

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, extra_env=None):
        env = os.environ
        if extra_env is not None:
            env.update(extra_env)
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], env=env, path="."
        )
        if code != 0:
            self.addDetail(
                "stdout", text_content(out.decode("UTF-8", "replace"))
            )
            self.addDetail(
                "stderr", text_content(err.decode("UTF-8", "replace"))
            )
            self.assertEqual(0, code)
        return out

    @defer.inlineCallbacks
    def test_fetch_via_proxy(self):
        remote_endpoint_listener = self.startFakeRemoteEndpoint()
        remote_endpoint_url = self.getListenerURL(remote_endpoint_listener)
        remote_proxy_listener = self.startFakeRemoteProxy()
        proxy_listener = self.startLocalProxy(
            self.getListenerURL(remote_proxy_listener)
        )
        out = yield self.assertCommandSuccess(
            [b"curl", remote_endpoint_url.encode("UTF-8") + b"x"],
            extra_env={"http_proxy": self.getListenerURL(proxy_listener)},
        )
        self.assertEqual(b"x" * 1024, out)
        out = yield self.assertCommandSuccess(
            [b"curl", remote_endpoint_url.encode("UTF-8") + b"y"],
            extra_env={"http_proxy": self.getListenerURL(proxy_listener)},
        )
        self.assertEqual(b"y" * 65536, out)

    # XXX cjwatson 2017-04-13: We should really test the HTTPS case as well,
    # but it's hard to see how to test that in a way that's independent of
    # the code under test since the stock twisted.web.proxy doesn't support
    # CONNECT.

    @responses.activate
    def test_revokeProxyToken(self):
        responses.add(
            "DELETE", f"http://proxy-auth.example/tokens/{self.buildid}"
        )
        self.buildmanager.revocation_endpoint = (
            f"http://proxy-auth.example/tokens/{self.buildid}"
        )
        self.buildmanager.proxy_url = "http://username:password@proxy.example"
        self.buildmanager.revokeProxyToken()
        self.assertEqual(1, len(responses.calls))
        request = responses.calls[0].request
        auth = base64.b64encode(b"username:password").decode()
        self.assertEqual(f"Basic {auth}", request.headers["Authorization"])
        self.assertEqual(
            f"http://proxy-auth.example/tokens/{self.buildid}", request.url
        )
        # XXX cjwatson 2023-02-07: Ideally we'd check the timeout as well,
        # but the version of responses in Ubuntu 20.04 doesn't store it
        # anywhere we can get at it.

    @responses.activate
    def test_revokeProxyToken_fetch_service(self):
        session_id = "123"

        responses.add(
            "DELETE",
            f"http://control.fetch-service.example/{session_id}/token",
        )

        self.buildmanager.use_fetch_service = True
        self.buildmanager.revocation_endpoint = (
            f"http://control.fetch-service.example/{session_id}/token"
        )
        self.buildmanager.proxy_url = (
            "http://session_id:token@proxy.fetch-service.example"
        )

        self.buildmanager.revokeProxyToken()

        self.assertEqual(1, len(responses.calls))
        request = responses.calls[0].request
        self.assertEqual(
            f"http://control.fetch-service.example/{session_id}/token",
            request.url,
        )
