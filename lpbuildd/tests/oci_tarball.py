import io
import json
import os
import tempfile
import tarfile


class OCITarball:
    """Create a tarball for use in tests with OCI."""

    def _makeFile(self, contents, name):
        json_contents = json.dumps(contents).encode("UTF-8")
        tarinfo = tarfile.TarInfo(name)
        tarinfo.size = len(json_contents)
        return tarinfo, io.BytesIO(json_contents)

    @property
    def config(self):
        return self._makeFile(
            {"rootfs": {"diff_ids": ["sha256:diff1", "sha256:diff2"]}},
            'config.json')

    @property
    def manifest(self):
        return self._makeFile(
            [{"Config": "config.json",
              "Layers": ["layer-1/layer.tar", "layer-2/layer.tar"]}],
            'manifest.json')

    @property
    def repositories(self):
        return self._makeFile([], 'repositories')

    def layer_file(self, directory, layer_name):
        contents = "{}-contents".format(layer_name)
        tarinfo = tarfile.TarInfo(contents)
        tarinfo.size = len(contents)
        layer_contents = io.BytesIO(contents.encode("UTF-8"))
        layer_tar_path = os.path.join(
            directory, '{}.tar.gz'.format(layer_name))
        layer_tar = tarfile.open(layer_tar_path, 'w:gz')
        layer_tar.addfile(tarinfo, layer_contents)
        layer_tar.close()
        return layer_tar_path

    def build_tar_file(self):
        tar_directory = tempfile.mkdtemp()
        tar_path = os.path.join(tar_directory, 'test-oci-image.tar')
        tar = tarfile.open(tar_path, 'w')
        tar.addfile(*self.config)
        tar.addfile(*self.manifest)
        tar.addfile(*self.repositories)

        for layer_name in ['layer-1', 'layer-2']:
            layer = self.layer_file(tar_directory, layer_name)
            tar.add(layer, arcname='{}.tar.gz'.format(layer_name))

        tar.close()

        return tar_path
