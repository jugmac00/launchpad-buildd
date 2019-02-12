# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from testtools import TestCase
from testtools.content import text_content
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    reactor,
    utils,
    )
from twisted.web import (
    http,
    proxy,
    resource,
    server,
    static,
    )

from lpbuildd.snap import (
    SnapBuildManager,
    SnapBuildState,
    SnapProxyFactory,
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

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

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

    @defer.inlineCallbacks
    def startBuild(self, args=None, options=None):
        # The build manager's iterate() kicks off the consecutive states
        # after INIT.
        extra_args = {
            "series": "xenial",
            "arch_tag": "i386",
            "name": "test-snap",
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
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

        # BUILD_SNAP: Run the slave's payload to build the snap package.
        yield self.buildmanager.iterate(0)
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        expected_command = [
            "sharepath/bin/in-target", "in-target", "buildsnap",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            "--git-repository", "https://git.launchpad.dev/~example/+git/snap",
            "--git-path", "master",
            ]
        if options is not None:
            expected_command.extend(options)
        expected_command.append("test-snap")
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

    @defer.inlineCallbacks
    def test_iterate(self):
        # The build manager iterates a normal build from start to finish.
        yield self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package.")

        # After building the package, reap processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target", "in-target", "scan-for-processes",
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
            "sharepath/bin/in-target", "in-target", "umount-chroot",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_manifest(self):
        # The build manager iterates a build that uploads a manifest from
        # start to finish.
        yield self.startBuild()

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package.")
        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.manifest", b"I am a manifest.")

        # After building the package, reap processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target", "in-target", "scan-for-processes",
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
            "sharepath/bin/in-target", "in-target", "umount-chroot",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))

    @defer.inlineCallbacks
    def test_iterate_with_build_source_tarball(self):
        # The build manager iterates a build that uploads a source tarball
        # from start to finish.
        yield self.startBuild(
            {"build_source_tarball": True}, ["--build-source-tarball"])

        log_path = os.path.join(self.buildmanager._cachepath, "buildlog")
        with open(log_path, "w") as log:
            log.write("I am a build log.")

        self.buildmanager.backend.add_file(
            "/build/test-snap/test-snap_0_all.snap", b"I am a snap package.")
        self.buildmanager.backend.add_file(
            "/build/test-snap.tar.gz", b"I am a source tarball.")

        # After building the package, reap processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target", "in-target", "scan-for-processes",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertThat(self.slave, HasWaitingFiles.byEquality({
            "test-snap_0_all.snap": b"I am a snap package.",
            "test-snap.tar.gz": b"I am a source tarball.",
            }))

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/bin/in-target", "in-target", "umount-chroot",
            "--backend=lxd", "--series=xenial", "--arch=i386", self.buildid,
            ]
        self.assertEqual(SnapBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))

    def getListenerURL(self, listener):
        port = listener.getHost().port
        return b"http://localhost:%d/" % port

    def startFakeRemoteEndpoint(self):
        remote_endpoint = resource.Resource()
        remote_endpoint.putChild("a", static.Data("a" * 1024, "text/plain"))
        remote_endpoint.putChild("b", static.Data("b" * 65536, "text/plain"))
        remote_endpoint_listener = reactor.listenTCP(
            0, server.Site(remote_endpoint))
        self.addCleanup(remote_endpoint_listener.stopListening)
        return remote_endpoint_listener

    def startFakeRemoteProxy(self):
        remote_proxy_factory = http.HTTPFactory()
        remote_proxy_factory.protocol = proxy.Proxy
        remote_proxy_listener = reactor.listenTCP(0, remote_proxy_factory)
        self.addCleanup(remote_proxy_listener.stopListening)
        return remote_proxy_listener

    def startLocalProxy(self, remote_url):
        proxy_factory = SnapProxyFactory(
            self.buildmanager, remote_url, timeout=60)
        proxy_listener = reactor.listenTCP(0, proxy_factory)
        self.addCleanup(proxy_listener.stopListening)
        return proxy_listener

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, extra_env=None):
        env = os.environ
        if extra_env is not None:
            env.update(extra_env)
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], env=env, path=".")
        if code != 0:
            self.addDetail("stdout", text_content(out))
            self.addDetail("stderr", text_content(err))
            self.assertEqual(0, code)
        defer.returnValue(out)

    @defer.inlineCallbacks
    def test_fetch_via_proxy(self):
        remote_endpoint_listener = self.startFakeRemoteEndpoint()
        remote_endpoint_url = self.getListenerURL(remote_endpoint_listener)
        remote_proxy_listener = self.startFakeRemoteProxy()
        proxy_listener = self.startLocalProxy(
            self.getListenerURL(remote_proxy_listener))
        out = yield self.assertCommandSuccess(
            [b"curl", remote_endpoint_url + b"a"],
            extra_env={b"http_proxy": self.getListenerURL(proxy_listener)})
        self.assertEqual("a" * 1024, out)
        out = yield self.assertCommandSuccess(
            [b"curl", remote_endpoint_url + b"b"],
            extra_env={b"http_proxy": self.getListenerURL(proxy_listener)})
        self.assertEqual("b" * 65536, out)

    # XXX cjwatson 2017-04-13: We should really test the HTTPS case as well,
    # but it's hard to see how to test that in a way that's independent of
    # the code under test since the stock twisted.web.proxy doesn't support
    # CONNECT.
