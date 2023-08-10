===========================
Production deployment notes
===========================

In Launchpad's production build farm, launchpad-buildd is deployed via base
virtual machine images constructed by taking standard Ubuntu cloud images
and installing launchpad-buildd in them from
https://launchpad.net/~canonical-is-sa/+archive/ubuntu/buildd.  This is done
by
https://code.launchpad.net/~canonical-sysadmins/canonical-is-charms/launchpad-buildd-image-modifier
(currently private, sorry).

We deliberately run builders in virtual machines rather than containers
for the following reasons:

- avoiding issues with nested containerization
- containers are not secure enough against being escaped by malicious code

------------------
Additional context
------------------

Charm recipe builds, `Launchpad CI`_, live filesystem builds, OCI recipe
builds, and snap recipe builds all build in LXD containers.
Everything else builds in chroots.

 .. _Launchpad CI: https://help.launchpad.net/Code/ContinuousIntegration
 