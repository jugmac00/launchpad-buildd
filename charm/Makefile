NAME ?= launchpad-buildd
CHARM_SERIES ?= 20.04
ARCH := $(shell dpkg --print-architecture)
CHARM = ./$(NAME)_ubuntu-$(CHARM_SERIES)-$(ARCH).charm


build: $(CHARM)

$(CHARM):
	charmcraft pack

clean:
	charmcraft clean
	rm -rf $(CHARM)

create-privileged-model:
	juju add-model privileged localhost
	lxc profile set juju-privileged security.privileged true

deploy:
	juju deploy $(CHARM)

.PHONY: build clean create-privileged-model deploy
