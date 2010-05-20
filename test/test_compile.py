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


def write_file(file,data):
    with open(file,'w') as f:
        f.write(data)

class TestCompile(unittest.TestCase):
    header_file = '''
        #include <string>
        #include <exception>

        class MyClass {
        public:
            unsigned int value;
            MyClass(unsigned int value);

            static std::string greet();
        };

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
        </module>
    '''

    @classmethod
    def modname(cls):
        return cls.__name__.lower()

    def setUp(self):
        self.olddir = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

        try:
            write_file('main.h',self.header_file)
            write_file('spec.xml',self.spec_file)

            pyinc = distutils.sysconfig.get_python_inc()

            spec = espec.getspec('spec.xml')
            spec.name = self.modname() # give the new module a unique name
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

    def compile(self):
        try:
            obj = self.comp.compile([self.modname() + '.cpp'],debug=True)
            self.comp.link_shared_lib(obj,self.modname(),debug=True)
        except (distutils.ccompiler.CompileError,distutils.ccompiler.LinkError) as e:
            self.fail(str(e))

        mname = self.comp.library_filename(self.modname(),'shared')
        name,ext = os.path.splitext(mname)
        if name != self.modname():
            os.rename(mname,self.modname()+ext)

        try:
            return __import__(self.modname())
        except Exception as e:
            self.fail(str(e))

    def runTest(self):
        tm = self.compile()

        self.assertTrue(tm.__dict__.get('AClass'))
        self.assertEqual(tm.__doc__,'module doc string')

        try:
            ac = tm.AClass(42)
        except Exception as e:
            self.fail(str(e))

        self.assertTrue(hasattr(ac,'greet'))
        self.assertEqual(ac.value,42)
        self.assertEqual(ac.greet(),'Hello World!')
        self.assertEqual(ac.__doc__,'class doc string')

        self.assertRaises(TypeError,tm.AClass,-1)
        self.assertRaises(RuntimeError,tm.AClass,3)


class TestInheritance(TestCompile):
    header_file = '''
        class BaseA {
        public:
            BaseA(int value) : value(value) {}
            virtual ~BaseA() {}
            int value_times(int x) { return value * x; }

            int value;
        };

        class BaseB : public BaseA {
        public:
            BaseB() : BaseA(3) {}
        };

        class BaseC {
        public:
            BaseC() : value(4) {}
            int value;

            double value_div(double x) { return double(value) / x; }
        };

        class Derived : public BaseB, public BaseC {
        public:

        };

        int add_nine(BaseC &bc) {
            bc.value += 9;
            return bc.value;
        }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="BaseA">
                <init/>
            </class>

            <class type="BaseB">
                <init/>
                <def func="value_times"/>
            </class>

            <class type="BaseC">
                <init/>
                <def func="value_div"/>
            </class>

            <class type="Derived">
                <init/>
            </class>

            <def func="add_nine"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        try:
            der = tm.Derived()
            self.assertEqual(der.value_times(5),15)
            self.assertAlmostEqual(der.value_div(5),0.8)
        except Exception as e:
            self.fail(str(e))

        self.assertRaises(TypeError,tm.BaseC.__init__,der)

        self.assertEqual(tm.add_nine(der),13)
        self.assertEqual(tm.add_nine(der),22)


class TestOverloading(TestCompile):
    header_file = '''
        int overloaded(float,float,float,float) { return 3; }
        double overloaded(int) { return 2e50; }
        const char *overloaded(int,float,const char*) { return "yellow submarine"; }
        float overloaded(int,float,int,int) { return 6.0f; }
        unsigned short different_name(int,int,const char*) { return 9; }

        void overload_1arg(int) {}
        float overload_1arg(float) { return 2.0; }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <def func="overloaded"/>
            <def name="overloaded" func="different_name"/>
            <def func="overload_1arg"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        try:
            self.assertEqual(tm.overloaded(1.0,2.0,3.0,4.0),3)
            self.assertAlmostEqual(tm.overloaded(1),2e50)
            self.assertEqual(tm.overloaded(1,2.0,"3"),"yellow submarine")
            self.assertAlmostEqual(tm.overloaded(1,2.0,3,4),6.0)
            self.assertEqual(tm.overloaded(1,2,"3"),9)

            self.assertEqual(tm.overload_1arg(1),None)
            self.assertAlmostEqual(tm.overload_1arg(1.0),2.0)
        except Exception as e:
            self.fail(str(e))



if __name__ == '__main__':
    unittest.main()
