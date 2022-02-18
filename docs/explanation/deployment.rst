Production deployment notes
***************************

In Launchpad's production build farm, launchpad-buildd is deployed via base
virtual machine images constructed by taking standard Ubuntu cloud images
and installing launchpad-buildd in them from
https://launchpad.net/~canonical-is-sa/+archive/ubuntu/buildd.  This is done
by
https://code.launchpad.net/~canonical-sysadmins/canonical-is-charms/launchpad-buildd-image-modifier
(currently private, sorry).
