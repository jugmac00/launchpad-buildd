# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import shutil
import tempfile

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
            "arch_tag": "i386",
            "name": "test-snap",
            "git_repository": "https://git.launchpad.dev/~example/+git/snap",
            "git_path": "master",
            }
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)

        # Skip states that are done in DebianBuildManager to the state
        # directly before BUILD_SNAP.
        self.buildmanager._state = SnapBuildState.UPDATE

        # BUILD_SNAP: Run the slave's payload to build the snap package.
        self.buildmanager.iterate(0)
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        expected_command = [
            "sharepath/slavebin/buildsnap", "buildsnap",
            "--build-id", self.buildid, "--arch", "i386",
            "--git-repository", "https://git.launchpad.dev/~example/+git/snap",
            "--git-path", "master",
            "test-snap",
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

        output_dir = os.path.join(self.build_dir, "test-snap")
        os.makedirs(output_dir)
        snap_path = os.path.join(output_dir, "test-snap_0_all.snap")
        with open(snap_path, "w") as snap:
            snap.write("I am a snap package.")

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/slavebin/scan-for-processes", "scan-for-processes",
            self.buildid,
            ]
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertEqual([((snap_path,), {})], self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/slavebin/umount-chroot", "umount-chroot", self.buildid]
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
        log = open(log_path, "w")
        log.write("I am a build log.")
        log.close()

        output_dir = os.path.join(self.build_dir, "test-snap")
        os.makedirs(output_dir)
        snap_path = os.path.join(output_dir, "test-snap_0_all.snap")
        with open(snap_path, "w") as snap:
            snap.write("I am a snap package.")
        manifest_path = os.path.join(output_dir, "test-snap_0_all.manifest")
        with open(manifest_path, "w") as manifest:
            manifest.write("I am a manifest.")

        # After building the package, reap processes.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/slavebin/scan-for-processes", "scan-for-processes",
            self.buildid,
            ]
        self.assertEqual(SnapBuildState.BUILD_SNAP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1])
        self.assertFalse(self.slave.wasCalled("buildFail"))
        self.assertEqual(
            [((manifest_path,), {}), ((snap_path,), {})],
            self.slave.addWaitingFile.calls)

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/slavebin/umount-chroot", "umount-chroot", self.buildid]
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
