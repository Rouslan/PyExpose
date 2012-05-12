#!/usr/bin/python


import subprocess
import tempfile
import os
import os.path
import shutil
from optparse import OptionParser

from gccxml import getinterface
from espec import getspec



def generate_module(spec,path,gccxml,compiler,cxxflags):
    """Run gccxml and save the results in outfile.

    spec -- an instance of espec.ModuleDef
    path -- the path to write temporary files in
    gccxml -- the path to gccxml
    compiler -- the compiler for gccxml to mimic (see the --gccxml-compiler flag)
    cxxflags -- compiler flags

    """
    gccinname = os.path.join(path,'in.cpp')
    gccoutname = os.path.join(path,'parsetree')

    with open(gccinname,'w') as gccin:
        spec.print_gccxml_input(gccin)

    args = [gccxml or "gccxml"]
    if compiler: args.extend(["--gccxml-compiler",compiler])
    if cxxflags: args.extend(["--gccxml-cxxflags",cxxflags])
    args.extend([gccinname,"-fxml="+gccoutname])
    subprocess.check_call(args)
    spec.write_file(path,getinterface(gccoutname))


if __name__ == '__main__':
    p = OptionParser(usage = "%prog [options] spec-file")
    p.add_option("--cxxflags",dest="cxxflags",help="CXXFLAGS passed to gccxml",action="append",metavar="ARGS")
    p.add_option("-c","--compiler",dest="compiler",help="the compiler to simulate (see the documentation for gccxml and the --gccxml-compiler option for more information)",metavar="TYPE")
    p.add_option("--gccxml",dest="gccxml",help="path to the gccxml executable",metavar="PATH")

    options,args = p.parse_args()

    if len(args) != 1:
        p.error("exactly 1 spec-file must be specified")

    spec = getspec(args[0])
    tdir = tempfile.mkdtemp()
    try:
        generate_module(spec, tdir, options.gccxml, options.compiler, options.cxxflags and " ".join(options.cxxflags))
    finally:
        shutil.rmtree(tdir)
