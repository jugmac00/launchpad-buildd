NAME ?= launchpad-buildd
TOPDIR ?= $(CURDIR)/..
BUILDDIR ?= $(CURDIR)/dist
TMPDIR ?= $(CURDIR)/tmp

CHARM_SERIES ?= bionic
CHARM_SRC ?= $(CURDIR)
JUJU_REPOSITORY = $(BUILDDIR)
CHARMDIR = $(BUILDDIR)/$(CHARM_SERIES)/$(NAME)
CHARMREPODIR = $(BUILDDIR)/build
RESOURCES = $(BUILDDIR)/resources/$(NAME)
CHARM = $(CHARMDIR)/.done
LAYER_PATH = $(TMPDIR)/layer
INTERFACE_PATH = $(TMPDIR)/interface
EXTRA_CHARM_BUILD_ARGS ?=
DEPLOY_ENV ?= devel
CHARM_STORE_URL ?= cs:~launchpad/launchpad-buildd

export INTERFACE_PATH
export LAYER_PATH
export JUJU_REPOSITORY


build: $(CHARM)

packages: $(RESOURCES)

build-with-packages: build packages

$(BUILDDIR):
	mkdir -p $@

$(CHARM): $(CHARM_SRC) | $(BUILDDIR)
	rm -rf $(CHARMDIR)
	charm build -o $(BUILDDIR) -s $(CHARM_SERIES) -n $(NAME) $(EXTRA_CHARM_BUILD_ARGS)
	touch $(CHARMDIR)/dummy-launchpad-buildd.deb
	touch $(CHARMDIR)/dummy-python3-lpbuildd.deb
	touch $@

clean:
	rm -rf $(BUILDDIR) $(TMPDIR)

$(RESOURCES):
	rm -rf $(TMPDIR)
	mkdir -p $(TMPDIR)
	rsync -a --exclude /charm $(TOPDIR)/ $(TMPDIR)/$(NAME)/
	cd $(TMPDIR)/$(NAME)/ && debuild -b -uc -us
	mkdir -p $(RESOURCES)
	cp -a $(TMPDIR)/launchpad-buildd_*.deb \
	      $(RESOURCES)/launchpad-buildd.deb
	cp -a $(TMPDIR)/python3-lpbuildd_*.deb $(RESOURCES)/python3-lpbuildd.deb

create-privileged-model:
	juju add-model privileged localhost
	lxc profile set juju-privileged security.privileged true

deploy:
	juju deploy $(CHARMDIR)

deploy-with-packages:
	juju deploy \
		--resource=launchpad-buildd=$(RESOURCES)/launchpad-buildd.deb \
		--resource=python3-lpbuildd=$(RESOURCES)/python3-lpbuildd.deb \
		$(CHARMDIR)

push:
	charm push $(CHARMDIR) $(CHARM_STORE_URL)

push-with-packages:
	charm push \
		--resource=launchpad-buildd=$(CHARMDIR)/dummy-launchpad-buildd.deb \
		--resource=python3-lpbuildd=$(CHARMDIR)/dummy-python3-lpbuildd.deb \
		$(CHARMDIR) $(CHARM_STORE_URL)

.PHONY: build packages build-with-packages clean
.PHONY: create-privileged-model deploy deploy-with-packages
.PHONY: push push-with-packages
