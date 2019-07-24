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

    def _gatherManifestSection(self, section, extract_path, sha_directory):
        config_file_path = os.path.join(extract_path, section["Config"])
        self._builder.addWaitingFile(config_file_path)
        with open(config_file_path, 'r') as config_fp:
            config = json.load(config_fp)
        diff_ids = config["rootfs"]["diff_ids"]
        digest_diff_map = {}
        for diff_id, layer_id in zip(diff_ids, section['Layers']):
            layer_id = layer_id.split('/')[0]
            diff_file = os.path.join(sha_directory, diff_id.split(':')[1])
            if not os.path.exists(diff_file):
                self._builder.addWaitingFile(
                    os.path.join(
                        extract_path,
                        "{}.tar.gz".format(layer_id)
                    )
                )
                continue
            with open(diff_file, 'r') as diff_fp:
                diff = json.load(diff_fp)
                # We should be able to just take the first occurence,
                # as that will be the 'most parent' image
                digest = diff[0]["Digest"]
                digest_diff_map[diff_id] = {
                    "digest": digest,
                    "source": diff[0]["SourceRepository"],
                }
        return digest_diff_map

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
                if directory_tar:
                    # Close the old directory if we have one
                    directory_tar.close()
                # We're going to add the layer.tar to a gzip
                directory_tar = tarfile.open(
                    os.path.join(
                        extract_path, '{}.tar.gz'.format(file.name)),
                    'w|gz')
            if current_dir and file.name.endswith('layer.tar'):
                # This is the actual layer data, we want to add it to
                # the directory gzip
                file.name = file.name.split('/')[1]
                directory_tar.addfile(file, tar.extractfile(file))
            elif current_dir and file.name.startswith(current_dir):
                # Other files that are in the layer directories,
                # we don't care about
                continue
            else:
                # If it's not in a directory, we need that
                tar.extract(file, extract_path)

        # We need these mapping files
        sha_directory = tempfile.mkdtemp()
        sha_path = ('/var/snap/docker/common/var-lib-docker/image/'
                    'aufs/distribution/v2metadata-by-diffid/sha256/')
        sha_files = [x for x in self.backend.listdir(sha_path)
                     if not x.startswith('.')]
        for file in sha_files:
            self.backend.copy_out(
                os.path.join(sha_path, file),
                os.path.join(sha_directory, file)
            )

        # Parse the manifest for the other files we need
        manifest_path = os.path.join(extract_path, 'manifest.json')
        self._builder.addWaitingFile(manifest_path)
        with open(manifest_path) as manifest_fp:
            manifest = json.load(manifest_fp)

        digest_maps = []
        for section in manifest:
            digest_maps.append(
                self._gatherManifestSection(section, extract_path,
                                            sha_directory))
        digest_map_file = os.path.join(extract_path, 'digests.json')
        with open(digest_map_file, 'w') as digest_map_fp:
            json.dump(digest_maps, digest_map_fp)
        self._builder.addWaitingFile(digest_map_file)
