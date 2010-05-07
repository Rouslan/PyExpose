#!/usr/bin/python

import os
import os.path
import sys
import shutil
import tempfile
import unittest
import distutils.ccompiler
import distutils.sysconfig


sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import expose
import espec

header_file = '''
#ifndef main_h
#define main_h

#include <string>

class MyClass {
public:
    unsigned int value;
    MyClass(unsigned int value);

    static std::string greet();
};

class BaseA {
public:
    BaseA(int value) : value(value) {}
    virtual ~BaseA();
    int value_times(int x) { return value * x; }

    int value;
};

class BaseB : public BaseA {
public:
    BaseB() : BaseA(3) {}
};

class BaseC : public BaseA {
    BaseC() : BaseA(4) {}
};

class Derived : public BaseA, public BaseB {
public:

};

#endif
'''

code_file = '''
#include "main.h"
#include <exception>

class NoThreesError : public std::exception {
public:
    const char *what() const throw() { return "No threes!"; }
};

MyClass::MyClass(unsigned int value) : value(value) {
    if(value == 3) throw NoThreesError();
}

std::string MyClass::greet() {
    return std::string("Hello World!");
}


BaseA::~BaseA() {}
'''

spec_file = '''<?xml version="1.0"?>
<module name="testmodule" include="main.h">
    <doc>module doc string</doc>

    <class name="AClass" type="MyClass">
        <doc>class doc string</doc>
        <init/>
        <member cmember="value"/>
        <def func="greet"/>
    </class>

    <class type="BaseA">
        <init/>
        <def func="value_times"/>
    </class>

    <class type="BaseB">
        <init/>
    </class>

    <class type="BaseC">
        <init/>
    </class>

    <class type="Derived">
        <init/>
    </class>
</module>
'''

def write_file(file,data):
    with open(file,'w') as f:
        f.write(data)

class TestCompile(unittest.TestCase):
    def setUp(self):
        self.olddir = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

        try:
            write_file('main.h',header_file)
            write_file('main.cpp',code_file)
            write_file('spec.xml',spec_file)

            pyinc = distutils.sysconfig.get_python_inc()

            spec = espec.getspec('spec.xml')
            expose.generate_intermediate(spec,'gccxml.interm','.','g++','-I'+pyinc)
            expose.generate_module(spec,'gccxml.interm','.')

            self.comp = distutils.ccompiler.new_compiler()
            distutils.sysconfig.customize_compiler(self.comp)
            self.comp.add_include_dir(pyinc)
            self.comp.add_library('stdc++')

            # add the current directory to the path so the module that will be generated, can be loaded
            sys.path.insert(0,self.dir)
        except:
            self.tearDown()
            raise

    def tearDown(self):
        os.chdir(self.olddir)
        shutil.rmtree(self.dir)

    def test_compile(self):
        try:
            obj = self.comp.compile(['main.cpp','testmodule.cpp'],debug=True)
        except distutils.ccompiler.CompileError as e:
            self.fail(str(e))

        try:
            self.comp.link_shared_lib(obj,'testmodule',debug=True)
        except distutils.ccompiler.LinkError as e:
            self.fail(str(e))


        mname = self.comp.library_filename('testmodule','shared')
        name,ext = os.path.splitext(mname)
        if name != 'testmodule':
            os.rename(mname,'testmodule'+ext)

        try:
            import testmodule
        except Exception as e:
            self.fail(str(e))

        self.assertTrue(testmodule.__dict__.get('AClass'))
        self.assertEqual(testmodule.__doc__,'module doc string')

        try:
            ac = testmodule.AClass(42)
        except Exception as e:
            self.fail(str(e))

        self.assertTrue(hasattr(ac,'greet'))
        self.assertEqual(ac.value,42)
        self.assertEqual(ac.greet(),'Hello World!')
        self.assertEqual(ac.__doc__,'class doc string')

        self.assertRaises(TypeError,testmodule.AClass,-1)
        self.assertRaises(RuntimeError,testmodule.AClass,3)

        try:
            der = testmodule.Derived()
            print der.value_times(5)
        except Exception as e:
            self.fail(str(e))



if __name__ == '__main__':
    unittest.main()
