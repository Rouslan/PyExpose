#!/usr/bin/python


import subprocess
import tempfile
import os
import os.path

from .gccxml import getinterface
from .espec import getspec



def generate_module(spec,path,gccxml=None,compiler=None,cxxflags=None):
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

    args = [gccxml or "gccxml",'-I.']
    if compiler: args.extend(["--gccxml-compiler",compiler])
    if cxxflags: args.extend(["--gccxml-cxxflags",cxxflags])
    args.extend([gccinname,"-fxml="+gccoutname])
    subprocess.check_call(args)
    spec.write_file(path,getinterface(gccoutname))


