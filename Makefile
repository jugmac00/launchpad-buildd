# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

all: deb

src: clean
	dpkg-buildpackage -rfakeroot -uc -us -S

deb: clean
	dpkg-buildpackage -rfakeroot -uc -us

clean:
	fakeroot debian/rules clean

realclean:
	rm -f ../launchpad-buildd*tar.gz
	rm -f ../launchpad-buildd*dsc
	rm -f ../launchpad-buildd*deb
	rm -f ../launchpad-buildd*changes

.PHONY: all clean deb install realclean src check docs

check:
	PYTHONPATH=$(CURDIR):$(PYTHONPATH) python3 -m testtools.run \
		discover -v

install:
	sudo add-apt-repository ppa:launchpad/ppa \
	&& sudo apt-get update \
	&& cat system-dependencies.txt | sudo xargs apt-get install -y \

install-build-deps: install
	sudo apt install -y dpkg-dev dh-exec dh-python

docs:
	sphinx-build -M html docs  docs/_build
