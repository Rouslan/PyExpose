#!/usr/bin/python


import subprocess
import tempfile
import os
import os.path
from optparse import OptionParser

from gccxml import getinterface
from espec import getspec



def generate_intermediate(spec,outfile,path,gccxml,compiler,cxxflags):
    """Run gccxml and save the results in outfile.

    spec -- an instance of espec.ModuleDef
    outfile -- the path where gccxml will save its output
    path -- the path that the include files specified in the spec file are relative to, or None
    gccxml -- the path to gccxml
    compiler -- the compiler for gccxml to mimic (see the --gccxml-compiler flag)
    cxxflags -- compiler flags

    """
    gccin,gccinname = tempfile.mkstemp(dir = path)
    gccin = os.fdopen(gccin,"w")

    try:
        try:
            spec.print_gccxml_input(gccin)

        finally:
            gccin.close()

        args = [gccxml or "gccxml"]
        if compiler: args.extend(["--gccxml-compiler",compiler])
        if cxxflags: args.extend(["--gccxml-cxxflags",cxxflags])
        args.extend([gccinname,"-fxml="+outfile])
        subprocess.check_call(args)

    finally:
        os.remove(gccinname)

def generate_module(spec,interm,path):
    spec.write_file(path,getinterface(interm))

if __name__ == "__main__":
    p = OptionParser(usage = "%prog [options] spec-file")
    p.add_option("--cxxflags",dest="cxxflags",help="CXXFLAGS passed to gccxml",action="append",metavar="ARGS")
    p.add_option("-c","--compiler",dest="compiler",help="the compiler to simulate (see the documentation for gccxml and the --gccxml-compiler option for more information)",metavar="TYPE")
    p.add_option("--gccxml",dest="gccxml",help="path to the gccxml executable",metavar="PATH")

    # TODO: remove this option in a release version, it only makes sense when this program is being modified
    p.add_option("-f","--reparse",dest="reparse",action="store_true",help="have gccxml reparse the header files even if the spec-file is not newer than gccxml.interm")

    options,args = p.parse_args()

    if len(args) != 1:
        p.error("exactly 1 spec-file must be specified")

    spec = getspec(args[0])
    path = os.path.dirname(args[0])
    gccoutname = os.path.join(path,"gccxml.interm")

    if options.reparse or (not os.path.exists(gccoutname)) or os.path.getmtime(args[0]) > os.path.getmtime(gccoutname):
        generate_intermediate(spec, gccoutname, path, options.gccxml, options.compiler, options.cxxflags and " ".join(options.cxxflags))

    generate_module(spec,gccoutname,path)
