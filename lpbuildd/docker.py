# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import base64
import json
import os
import tempfile

from six.moves.configparser import (
    NoOptionError,
    NoSectionError,
    )
from six.moves.urllib.error import (
    HTTPError,
    URLError,
    )
from six.moves.urllib.parse import urlparse
from six.moves.urllib.request import (
    Request,
    urlopen,
    )
from twisted.application import strports

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    )
from lpbuildd.snap import SnapProxyFactory


RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


class DockerBuildState(DebianBuildState):
    BUILD_DOCKER = "BUILD_DOCKER"


class DockerBuildManager(DebianBuildManager):
    """Build a snap."""

    backend_name = "lxd"
    initial_build_state = DockerBuildState.BUILD_DOCKER

    @property
    def needs_sanitized_logs(self):
        return True

    def initiate(self, files, chroot, extra_args):
        """Initiate a build with a given set of files and chroot."""
        self.name = extra_args["name"]
        self.branch = extra_args.get("branch")
        self.git_repository = extra_args.get("git_repository")
        self.git_path = extra_args.get("git_path")
        self.file = extra_args.get("file")
        self.proxy_url = extra_args.get("proxy_url")
        self.revocation_endpoint = extra_args.get("revocation_endpoint")
        self.proxy_service = None

        super(DockerBuildManager, self).initiate(files, chroot, extra_args)

    def startProxy(self):
        """Start the local snap proxy, if necessary."""
        if not self.proxy_url:
            return []
        proxy_port = self._builder._config.get("snapmanager", "proxyport")
        proxy_factory = SnapProxyFactory(self, self.proxy_url, timeout=60)
        self.proxy_service = strports.service(proxy_port, proxy_factory)
        self.proxy_service.setServiceParent(self._builder.service)
        if self.backend_name == "lxd":
            proxy_host = self.backend.ipv4_network.ip
        else:
            proxy_host = "localhost"
        return ["--proxy-url", "http://{}:{}/".format(proxy_host, proxy_port)]

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
        self._builder.log("Revoking proxy token...\n")
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
            self._builder.log(
                "Unable to revoke token for %s: %s" % (url.username, e))

    def doRunBuild(self):
        """Run the process to build the snap."""
        args = []
        args.extend(self.startProxy())
        if self.revocation_endpoint:
            args.extend(["--revocation-endpoint", self.revocation_endpoint])
        if self.branch is not None:
            args.extend(["--branch", self.branch])
        if self.git_repository is not None:
            args.extend(["--git-repository", self.git_repository])
        if self.git_path is not None:
            args.extend(["--git-path", self.git_path])
        if self.file is not None:
            args.extend(["--file", self.file])
        try:
            snap_store_proxy_url = self._builder._config.get(
                "proxy", "snapstore")
            args.extend(["--snap-store-proxy-url", snap_store_proxy_url])
        except (NoSectionError, NoOptionError):
            pass
        args.append(self.name)
        self.runTargetSubProcess("build-docker", *args)

    def iterate_BUILD_DOCKER(self, retcode):
        """Finished building the Docker image."""
        self.stopProxy()
        self.revokeProxyToken()
        if retcode == RETCODE_SUCCESS:
            print("Returning build status: OK")
            return self.deferGatherResults()
        elif (retcode >= RETCODE_FAILURE_INSTALL and
              retcode <= RETCODE_FAILURE_BUILD):
            if not self.alreadyfailed:
                self._builder.buildFail()
                print("Returning build status: Build failed.")
            self.alreadyfailed = True
        else:
            if not self.alreadyfailed:
                self._builder.builderFail()
                print("Returning build status: Builder failed.")
            self.alreadyfailed = True
        self.doReapProcesses(self._state)

    def iterateReap_BUILD_DOCKER(self, retcode):
        """Finished reaping after building the Docker image."""
        self._state = DebianBuildState.UMOUNT
        self.doUnmounting()

    def gatherResults(self):
        """Gather the results of the build and add them to the file cache."""
        self.addWaitingFileFromBackend('/build/manifest.json')
        with tempfile.NamedTemporaryFile() as manifest_path:
            self.backend.copy_out('/build/manifest.json', manifest_path.name)
            with open(manifest_path.name) as manifest_fp:
                manifest = json.load(manifest_fp)

        print(manifest)

        for section in manifest:
            layers = section['Layers']
            for layer in layers:
                layer_name = layer.split('/')[0]
                layer_path = os.path.join('/build/', layer_name + '.tar')
                self.addWaitingFileFromBackend(layer_path)
