# Copyright 2009-2011 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

# XXX: dsilvers: 2005/01/21: Currently everything logged in the builder gets
# passed through to the twistd log too. this could get dangerous/big

try:
    from configparser import ConfigParser as SafeConfigParser
except ImportError:
    from ConfigParser import SafeConfigParser
import os

from twisted.application import (
    service,
    strports,
    )
from twisted.scripts.twistd import ServerOptions
from twisted.web import (
    resource,
    server,
    static,
    )

from lpbuildd.binarypackage import BinaryPackageBuildManager
from lpbuildd.builder import XMLRPCBuilder
from lpbuildd.charm import CharmBuildManager
from lpbuildd.oci import OCIBuildManager
from lpbuildd.livefs import LiveFilesystemBuildManager
from lpbuildd.log import RotatableFileLogObserver
from lpbuildd.snap import SnapBuildManager
from lpbuildd.sourcepackagerecipe import SourcePackageRecipeBuildManager
from lpbuildd.translationtemplates import TranslationTemplatesBuildManager


options = ServerOptions()
options.parseOptions()

conffile = os.environ.get('BUILDD_SLAVE_CONFIG', 'buildd-slave-example.conf')

conf = SafeConfigParser()
conf.read(conffile)
builder = XMLRPCBuilder(conf)

builder.registerManager(BinaryPackageBuildManager, "binarypackage")
builder.registerManager(SourcePackageRecipeBuildManager, "sourcepackagerecipe")
builder.registerManager(
    TranslationTemplatesBuildManager, 'translation-templates')
builder.registerManager(LiveFilesystemBuildManager, "livefs")
builder.registerManager(SnapBuildManager, "snap")
builder.registerManager(OCIBuildManager, "oci")
builder.registerManager(CharmBuildManager, "charm")

application = service.Application('Builder')
application.addComponent(
    RotatableFileLogObserver(options.get('logfile')), ignoreClass=1)
builderService = service.IServiceCollection(application)
builder.builder.service = builderService

root = resource.Resource()
root.putChild(b'rpc', builder)
root.putChild(b'filecache', static.File(conf.get('builder', 'filecache')))
buildersite = server.Site(root)

strports.service("tcp:%s" % builder.builder._config.get("builder", "bindport"),
                 buildersite).setServiceParent(builderService)

# You can interact with a running builder like this:
# (assuming the builder is on localhost:8221)
#
# python3
# from xmlrpc.client import ServerProxy
# s = ServerProxy("http://localhost:8221/rpc")
# s.echo("Hello World")
