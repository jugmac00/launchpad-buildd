# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import base64
import io
import os
import shutil
try:
    from urllib.error import (
        HTTPError,
        URLError,
        )
    from urllib.parse import urlparse
    from urllib.request import (
        Request,
        urlopen,
        )
except ImportError:
    from urllib2 import (
        HTTPError,
        Request,
        URLError,
        urlopen,
        )
    from urlparse import urlparse

from twisted.application import strports
from twisted.internet import reactor
from twisted.internet.interfaces import IHalfCloseableProtocol
from twisted.python.compat import intToBytes
from twisted.web import (
    http,
    proxy,
    )
from zope.interface import implementer

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    get_build_path,
    )


RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


class SnapProxyClient(proxy.ProxyClient):

    def __init__(self, command, rest, version, headers, data, father):
        proxy.ProxyClient.__init__(
            self, command, rest, version, headers, data, father)
        # Why doesn't ProxyClient at least store this?
        self.version = version
        # We must avoid calling self.father.finish in the event that its
        # connection was already lost, i.e. if the original client
        # disconnects first (which is particularly likely in the case of
        # CONNECT).
        d = self.father.notifyFinish()
        d.addBoth(self.requestFinished)

    def connectionMade(self):
        proxy.ProxyClient.connectionMade(self)
        self.father.setChildClient(self)

    def sendCommand(self, command, path):
        # For some reason, HTTPClient.sendCommand doesn't preserve the
        # protocol version.
        self.transport.writeSequence(
            [command, b' ', path, b' ', self.version, b'\r\n'])

    def handleEndHeaders(self):
        self.father.handleEndHeaders()

    def sendData(self, data):
        self.transport.write(data)

    def endData(self):
        if self.transport is not None:
            self.transport.loseWriteConnection()

    def requestFinished(self, result):
        self._finished = True
        self.transport.loseConnection()


class SnapProxyClientFactory(proxy.ProxyClientFactory):

    protocol = SnapProxyClient


class SnapProxyRequest(http.Request):

    child_client = None
    _request_buffer = None
    _request_data_done = False

    def setChildClient(self, child_client):
        self.child_client = child_client
        if self._request_buffer is not None:
            self.child_client.sendData(self._request_buffer.getvalue())
            self._request_buffer = None
        if self._request_data_done:
            self.child_client.endData()

    def allHeadersReceived(self, command, path, version):
        # Normally done in `requestReceived`, but we disable that since it
        # does other things we don't want.
        self.method, self.uri, self.clientproto = command, path, version
        self.client = self.channel.transport.getPeer()
        self.host = self.channel.transport.getHost()

        remote_parsed = urlparse(self.channel.factory.remote_url)
        request_parsed = urlparse(path)
        headers = self.getAllHeaders().copy()
        if b"host" not in headers and request_parsed.netloc:
            headers[b"host"] = request_parsed.netloc
        if remote_parsed.username:
            auth = (remote_parsed.username + ":" +
                    remote_parsed.password).encode("ASCII")
            authHeader = b"Basic " + base64.b64encode(auth)
            headers[b"proxy-authorization"] = authHeader
        self.client_factory = SnapProxyClientFactory(
            command, path, version, headers, b"", self)
        reactor.connectTCP(
            remote_parsed.hostname, remote_parsed.port, self.client_factory)

    def requestReceived(self, command, path, version):
        # We do most of our work in `allHeadersReceived` instead.
        pass

    def rawDataReceived(self, data):
        if self.child_client is not None:
            if not self._request_data_done:
                self.child_client.sendData(data)
        else:
            if self._request_buffer is None:
                self._request_buffer = io.BytesIO()
            self._request_buffer.write(data)

    def handleEndHeaders(self):
        # Cut-down version of Request.write.  We must avoid switching to
        # chunked encoding for the sake of CONNECT; since our actual
        # response data comes from another proxy, we can cut some corners.
        if self.startedWriting:
            return
        self.startedWriting = 1
        l = []
        l.append(
            self.clientproto + b" " + intToBytes(self.code) + b" " +
            self.code_message + b"\r\n")
        for name, values in self.responseHeaders.getAllRawHeaders():
            for value in values:
                l.extend([name, b": ", value, b"\r\n"])
        l.append(b"\r\n")
        self.transport.writeSequence(l)

    def write(self, data):
        if self.channel is not None:
            self.channel.resetTimeout()
        http.Request.write(self, data)

    def endData(self):
        if self.child_client is not None:
            self.child_client.endData()
        self._request_data_done = True


@implementer(IHalfCloseableProtocol)
class SnapProxy(http.HTTPChannel):
    """A channel that streams request data.

    The stock HTTPChannel isn't quite suitable for our needs, because it
    expects to read the entire request data before passing control to the
    request.  This doesn't work well for CONNECT.
    """

    requestFactory = SnapProxyRequest

    def checkPersistence(self, request, version):
        # ProxyClient.__init__ forces "Connection: close".
        return False
        if self._command == b"CONNECT":
            return False
        else:
            return http.HTTPChannel.checkPersistence(self, request, version)

    def allHeadersReceived(self):
        http.HTTPChannel.allHeadersReceived(self)
        self.requests[-1].allHeadersReceived(
            self._command, self._path, self._version)
        if self._command == b"CONNECT":
            # This is a lie, but we don't want HTTPChannel to decide that
            # the request is finished just because a CONNECT request
            # (naturally) has no Content-Length.
            self.length = -1

    def rawDataReceived(self, data):
        self.resetTimeout()
        if self.requests:
            self.requests[-1].rawDataReceived(data)

    def readConnectionLost(self):
        for request in self.requests:
            request.endData()

    def writeConnectionLost(self):
        pass


class SnapProxyFactory(http.HTTPFactory):

    protocol = SnapProxy

    def __init__(self, manager, remote_url, *args, **kwargs):
        http.HTTPFactory.__init__(self, *args, **kwargs)
        self.manager = manager
        self.remote_url = remote_url
        # Hack for compatibility with the old version of Twisted that
        # Launchpad currently uses, for the benefit of tests.
        try:
            from twisted.web.http import _escape
            self._log_escape = _escape
        except ImportError:
            self._log_escape = self._escape

    def log(self, request):
        # Log requests to the build log rather than to Twisted.
        # Reimplement log formatting partly to make it easier to stay
        # compatible with the old version of Twisted that Launchpad
        # currently uses, and partly because there's no point logging the IP
        # here.
        referrer = self._log_escape(request.getHeader(b"referer") or b"-")
        agent = self._log_escape(request.getHeader(b"user-agent") or b"-")
        line = (
            u'%(timestamp)s "%(method)s %(uri)s %(protocol)s" '
            u'%(code)d %(length)s "%(referrer)s" "%(agent)s"\n' % {
                'timestamp': self._logDateTime,
                'method': self._log_escape(request.method),
                'uri': self._log_escape(request.uri),
                'protocol': self._log_escape(request.clientproto),
                'code': request.code,
                'length': request.sentLength or "-",
                'referrer': referrer,
                'agent': agent,
                })
        self.manager._slave.log(line.encode("UTF-8"))


class SnapBuildState(DebianBuildState):
    BUILD_SNAP = "BUILD_SNAP"


class SnapBuildManager(DebianBuildManager):
    """Build a snap."""

    initial_build_state = SnapBuildState.BUILD_SNAP

    def __init__(self, slave, buildid, **kwargs):
        super(SnapBuildManager, self).__init__(slave, buildid, **kwargs)
        self.build_snap_path = os.path.join(self._slavebin, "buildsnap")

    @property
    def needs_sanitized_logs(self):
        return True

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.build_path = get_build_path(
            self.home, self._buildid, "chroot-autobuild", "build")
        if os.path.isdir(self.build_path):
            shutil.rmtree(self.build_path)

        self.name = extra_args["name"]
        self.branch = extra_args.get("branch")
        self.git_repository = extra_args.get("git_repository")
        self.git_path = extra_args.get("git_path")
        self.proxy_url = extra_args.get("proxy_url")
        self.revocation_endpoint = extra_args.get("revocation_endpoint")
        self.proxy_service = None

        super(SnapBuildManager, self).initiate(files, chroot, extra_args)

    def startProxy(self):
        """Start the local snap proxy, if necessary."""
        if not self.proxy_url:
            return []
        proxy_port = self._slave._config.get("snapmanager", "proxyport")
        proxy_factory = SnapProxyFactory(self, self.proxy_url, timeout=60)
        self.proxy_service = strports.service(proxy_port, proxy_factory)
        self.proxy_service.setServiceParent(self._slave.service)
        return ["--proxy-url", "http://localhost:{}/".format(proxy_port)]

    def stopProxy(self):
        """Stop the local snap proxy, if necessary."""
        if self.proxy_service is None:
            return
        self.proxy_service.disownServiceParent()
        self.proxy_service = None

    def revokeProxyToken(self):
        """Revoke builder proxy token."""
        if not self.revocation_endpoint:
            return
        self._slave.log("Revoking proxy token...\n")
        url = urlparse(self.proxy_url)
        auth = "{}:{}".format(url.username, url.password)
        headers = {
            "Authorization": "Basic {}".format(base64.b64encode(auth))
            }
        req = Request(self.revocation_endpoint, None, headers)
        req.get_method = lambda: "DELETE"
        try:
            urlopen(req)
        except (HTTPError, URLError) as e:
            self._slave.log(
                "Unable to revoke token for %s: %s" % (url.username, e))

    def doRunBuild(self):
        """Run the process to build the snap."""
        args = [
            "buildsnap",
            "--build-id", self._buildid,
            "--arch", self.arch_tag,
            ]
        args.extend(self.startProxy())
        if self.revocation_endpoint:
            args.extend(["--revocation-endpoint", self.revocation_endpoint])
        if self.branch is not None:
            args.extend(["--branch", self.branch])
        if self.git_repository is not None:
            args.extend(["--git-repository", self.git_repository])
        if self.git_path is not None:
            args.extend(["--git-path", self.git_path])
        args.append(self.name)
        self.runSubProcess(self.build_snap_path, args)

    def iterate_BUILD_SNAP(self, retcode):
        """Finished building the snap."""
        self.stopProxy()
        self.revokeProxyToken()
        if retcode == RETCODE_SUCCESS:
            self.gatherResults()
            print("Returning build status: OK")
        elif (retcode >= RETCODE_FAILURE_INSTALL and
              retcode <= RETCODE_FAILURE_BUILD):
            if not self.alreadyfailed:
                self._slave.buildFail()
                print("Returning build status: Build failed.")
            self.alreadyfailed = True
        else:
            if not self.alreadyfailed:
                self._slave.builderFail()
                print("Returning build status: Builder failed.")
            self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_BUILD_SNAP(self, retcode):
        """Finished reaping after building the snap."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache."""
        output_path = os.path.join(self.build_path, self.name)
        if not os.path.exists(output_path):
            return
        for entry in sorted(os.listdir(output_path)):
            path = os.path.join(output_path, entry)
            if os.path.islink(path):
                continue
            if entry.endswith(".snap") or entry.endswith(".manifest"):
                self._slave.addWaitingFile(path)
