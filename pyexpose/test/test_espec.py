#!/usr/bin/env python

import unittest
import os
import os.path
import tempfile
import sys


from pyexpose import gccxml
from pyexpose import expose
from pyexpose.espec import *


toparse = '''
typedef bool type_bool;
typedef signed int type_sint;
typedef unsigned int type_uint;
typedef signed short type_sshort;
typedef unsigned short type_ushort;
typedef signed long type_slong;
typedef unsigned long type_ulong;
typedef float type_float;
typedef double type_double;
typedef long double type_long_double;
typedef signed char type_schar;
typedef unsigned char type_uchar;
typedef char type_char;
typedef wchar_t type_wchar_t;
typedef void type_void;

typedef int type_size_t;
typedef int type_py_unicode;
typedef int type_stdstring;
typedef int type_stdwstring;
'''

class DummyModuleDef:
    def print_gccxml_input(self,f):
        print >> f, toparse

class TestEspec(unittest.TestCase):
    def setUp(self):
        res = tempfile.NamedTemporaryFile('w',delete=False)
        try:
            res.close()
            expose.generate_intermediate(DummyModuleDef(),res.name,None,'g++','')
            cppint = gccxml.getinterface(res.name)
        finally:
            # gccxml will delete its output if it encounters an error
            if os.path.exists(res.name):
                os.remove(res.name)

        self.conv = Conversion(cppint)

    def test_generate_arg_tree(self):
        int = self.conv.sint
        float = self.conv.float
        cstr = self.conv.cstring

        def Arg(t):
            r = gccxml.CPPArgument()
            r.name = None
            r.type = t
            r.default = None
            return r

        argslist = [
            ('v',map(Arg,[int,float,cstr])),
            ('w',map(Arg,[int,float,int,int])),
            ('x',map(Arg,[int,int,cstr])),
            ('y',map(Arg,[int])),
            ('z',map(Arg,[float,float,float,float]))
        ]

        tree = self.conv.generate_arg_tree([(x[1],x) for x in argslist])

        self.assertTrue(isinstance(tree,ArgBranchNode))

        self.assertTrue(tree.basic[TYPE_INT])
        self.assertTrue(tree.basic[TYPE_FLOAT])
        self.assertEqual(tree.basic[TYPE_LONG],None)
        self.assertEqual(tree.basic[TYPE_STR],None)
        self.assertEqual(tree.basic[TYPE_UNICODE],None)
        self.assertEqual(len(tree.objects),0)

        self.assertTrue(tree.basic[TYPE_INT].basic[TYPE_INT])
        self.assertTrue(tree.basic[TYPE_INT].basic[TYPE_FLOAT])
        self.assertTrue(tree.basic[TYPE_FLOAT].basic[TYPE_FLOAT])

        self.assertTrue(tree.basic[TYPE_INT].basic[TYPE_INT].basic[TYPE_STR])
        self.assertTrue(tree.basic[TYPE_INT].basic[TYPE_FLOAT].basic[TYPE_INT])
        self.assertTrue(tree.basic[TYPE_INT].basic[TYPE_FLOAT].basic[TYPE_STR])
        self.assertTrue(tree.basic[TYPE_FLOAT].basic[TYPE_FLOAT].basic[TYPE_FLOAT])

        self.assertTrue(tree.basic[TYPE_INT].basic[TYPE_FLOAT].basic[TYPE_INT].basic[TYPE_INT])
        self.assertTrue(tree.basic[TYPE_FLOAT].basic[TYPE_FLOAT].basic[TYPE_FLOAT].basic[TYPE_FLOAT])

        self.assertEqual(tree.basic[TYPE_INT].basic[TYPE_FLOAT].basic[TYPE_STR].call,argslist[0])
        self.assertEqual(tree.basic[TYPE_INT].basic[TYPE_FLOAT].basic[TYPE_INT].basic[TYPE_INT].call,argslist[1])
        self.assertEqual(tree.basic[TYPE_INT].basic[TYPE_INT].basic[TYPE_STR].call,argslist[2])
        self.assertEqual(tree.basic[TYPE_INT].call,argslist[3])
        self.assertEqual(tree.basic[TYPE_FLOAT].basic[TYPE_FLOAT].basic[TYPE_FLOAT].basic[TYPE_FLOAT].call,argslist[4])

        self.assertEqual(tree.max_arg_length(),4)
        self.assertEqual(tree.min_arg_length(),1)
        self.assertEqual(tree.basic[TYPE_INT].max_arg_length(),3)
        self.assertEqual(tree.basic[TYPE_INT].min_arg_length(),0)

if __name__ == '__main__':
    unittest.main()
