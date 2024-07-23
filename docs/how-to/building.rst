How to build the project
************************

You can use `make` to build the package:

.. code:: bash

    make install-build-deps
    make deb

To clean up, there are 2 targets:

`clean`: It simulates root privileges to remove build artifacts and reset the source directory to a clean state

`realclean`: Removes additional artifacts (.deb, .dsc, .changes, .tar.gz) in the parent directory. 

.. code:: bash

    make clean
    make realclean