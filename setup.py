#!/usr/bin/python3

# Copyright 2015-2019 Canonical Ltd.  All rights reserved.
#
# This file is part of launchpad-buildd.
#
# launchpad-buildd is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, version 3 of the License.
#
# launchpad-buildd is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public
# License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with launchpad-buildd.  If not, see <http://www.gnu.org/licenses/>.

import re
from textwrap import dedent

from setuptools import find_packages, setup

changelog_heading = re.compile(r"\w[-+0-9a-z.]* \(([^\(\) \t]+)\)")

with open("debian/changelog") as changelog:
    line = changelog.readline()
    match = changelog_heading.match(line)
    if match is None:
        raise ValueError(
            "Failed to parse first line of debian/changelog: '%s'" % line
        )

    version = match.group(1)

    # Versions are picked from debian/changelog file.
    # With setuptools >= 66, it's mandated to follow PEP 440
    # based versioning for python packages
    try:
        # version naming on Launchpad recipe,
        # follows {debupstream}~{revno}~{ubuntu-release-suffix}
        # we just need the {debupstream}
        version = version.split("~")[0]

    # TODO: use packaging.version module to test whether PEP 440 is followed
    # This can be done after we deprecate focal, as packaging does not come
    # explicitly installed with setuptools in python 3.8 (ubuntu-focal)
    except IndexError:
        raise ValueError("Invalid version format in debian/changelog")


setup(
    name="launchpad-buildd",
    version=version,
    description="Launchpad buildd slave",
    long_description=dedent(
        """
        The Launchpad buildd slave libraries.  The PyPI version of this
        package will not produce a complete installation on its own, and is
        mostly useful for testing other pieces of software against
        launchpad-buildd; for a real Launchpad buildd slave, install the
        launchpad-buildd package from ppa:launchpad/ubuntu/ppa.
        """
    ).strip(),
    url="https://launchpad.net/launchpad-buildd",
    packages=find_packages(),
    include_package_data=True,
    maintainer="Launchpad Developers",
    maintainer_email="launchpad-dev@lists.launchpad.net",
    license="Affero GPL v3",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    install_requires=[
        # XXX cjwatson 2015-11-04: This does in fact require python-apt, but
        # that's normally shipped as a system package and specifying it here
        # causes problems for Launchpad's build system.
        # 'python-apt',
        "python-debian",
        "requests",
        "Twisted",
        "zope.interface",
    ],
    extras_require={
        "lxd": ["netaddr", "pylxd"],
        "docs": ["sphinx"],
    },
    test_suite="lpbuildd",
    tests_require=[
        "fixtures",
        "responses",
        "systemfixtures",
        "testtools",
        "txfixtures",
    ],
)
