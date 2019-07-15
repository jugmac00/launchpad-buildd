# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import json
import os
import tarfile
import tempfile

from six.moves.configparser import (
    NoOptionError,
    NoSectionError,
    )

from lpbuildd.debian import (
    DebianBuildManager,
    DebianBuildState,
    )
from lpbuildd.snap import SnapBuildProxyMixin


RETCODE_SUCCESS = 0
RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


class DockerBuildState(DebianBuildState):
    BUILD_DOCKER = "BUILD_DOCKER"


class DockerBuildManager(SnapBuildProxyMixin, DebianBuildManager):
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
        extract_path = tempfile.mkdtemp(prefix=self.name)
        proc = self.backend.run(
            ['docker', 'save', self.name],
            get_output=True, universal_newlines=False, return_process=True)
        tar = tarfile.open(fileobj=proc.stdout, mode="r|")

        current_dir = ''
        directory_tar = None
        # The tarfile is a stream and must be processed in order
        for file in tar:
            # Directories are just nodes, you can't extract the children
            # directly, so keep track of what dir we're in.
            if file.isdir():
                current_dir = file.name
                # Close the old directory if we have one
                if directory_tar:
                    directory_tar.close()
                # Extract each directory to a new tar, streaming in from
                # the image tar
                directory_tar = tarfile.open(
                    os.path.join(
                        extract_path, '{}.tar.gz'.format(file.name)),
                    'w|gz')
            # If this is a file, and it's in a directory, save it to
            # the new tar file
            elif current_dir and file.name.startswith(current_dir):
                directory_tar.addfile(file, tar.extractfile(file))
            # If it's not in a directory, just save it to the root
            else:
                tar.extract(file, extract_path)

        # This always exists
        self._builder.addWaitingFile(
            os.path.join(extract_path, 'repositories'))
        # Parse the manifest for the other files we need
        manifest_path = os.path.join(extract_path, 'manifest.json')
        self._builder.addWaitingFile(manifest_path)
        with open(manifest_path) as manifest_fp:
            manifest = json.load(manifest_fp)

        for section in manifest:
            # This has an ID as it's filename, specified in the manifest
            self._builder.addWaitingFile(
                os.path.join(extract_path, section["Config"]))
            layers = section['Layers']
            # We've extracted the layers to their own tar files, so add them
            # based on the layer ID/filename
            for layer in layers:
                layer_name = layer.split('/')[0]
                layer_path = os.path.join(extract_path, layer_name + '.tar.gz')
                self._builder.addWaitingFile(layer_path)
