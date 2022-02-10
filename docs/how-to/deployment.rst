How to deploy launchpad-buildd
******************************

The following steps need to be performed before `Upgrading the builders
<https://wiki.canonical.com/InformationInfrastructure/ISO/BuildInfrastructure/BuilddFixing>`_.

1. Ensure everything has been merged to master.

2. Create a new release branch, based on master.

3. Run ``DEBEMAIL="<email address>" DEBFULLNAME="<name>" dch -rD bionic``.
   The later recipe build will prepend the correct preamble for each Ubuntu release.

4. Commit and push this branch, open a MergeProposal for review.

5. Once the release branch has merged to master,
   tag the release commit and check https://code.launchpad.net/~launchpad/+recipe/launchpad-buildd-daily
   for the recipe build to happen.
   You can start a build if required.

6. Upgrade the dogfood builders
   (you may need someone on the LP team with permissions to help with this;
   see `documentation <https://wiki.canonical.com/InformationInfrastructure/ISO/BuildInfrastructure/BuilddFixing#Upgrading_launchpad-buildd_in_scalingstack>`_).

7. Perform QA

8. File an upgrade RT (`sample <https://portal.admin.canonical.com/C135751>`_),
   noting the version number and possibly multiple suites/releases
   (`IS procedure <https://wiki.canonical.com/InformationInfrastructure/ISO/BuildInfrastructure/BuilddFixing>`_).
