# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import re


non_meta_re = re.compile(r'^[a-zA-Z0-9+,./:=@_-]+$')

def shell_escape(arg):
    if non_meta_re.match(arg):
        return arg
    else:
        return "'%s'" % arg.replace("'", "'\\''")


linux32_arches = [
    "armel",
    "armhf",
    "hppa",
    "i386",
    "lpia",
    "mips",
    "mipsel",
    "powerpc",
    "s390",
    "sparc",
    ]
linux64_arches = [
    "alpha",
    "amd64",
    "arm64",
    "hppa64",
    "ia64",
    "ppc64",
    "ppc64el",
    "s390x",
    "sparc64",
    "x32",
    ]


def set_personality(arch, args):
    if arch in linux32_arches:
        return ["linux32"] + args
    elif arch in linux64_arches:
        return ["linux64"] + args
    else:
        return args
