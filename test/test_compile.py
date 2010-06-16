#!/usr/bin/python

import os
import os.path
import sys
import shutil
import tempfile
import unittest
import distutils.ccompiler
import distutils.sysconfig
import gc


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
                <attr cmember="value"/>
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
            expose.generate_intermediate(spec,'gccxml.interm','.',None,'g++','-I'+pyinc)
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


class TestExample(TestCompile):
    """The example from the documentation.

    This of all things should work.

    """
    header_file = ''
    spec_file = '''<?xml version="1.0"?>
        <module name="modulename" include="vector">
            <doc>module doc string</doc>

            <class name="DVector" type="std::vector&lt;double&gt;">
                <doc>class doc string</doc>
                <init overload=""/>
                <init overload="size_t,const double&amp;"/>
                <property name="size" get="size" set="resize"/>
                <def func="push_back"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        v = tm.DVector()
        self.assertEqual(v.size,0)
        v.push_back(3)
        self.assertEqual(v.size,1)


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

        der = tm.Derived()
        self.assertEqual(der.value_times(5),15)
        self.assertAlmostEqual(der.value_div(5),0.8)

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

        self.assertEqual(tm.overloaded(1.0,2.0,3.0,4.0),3)
        self.assertAlmostEqual(tm.overloaded(1),2e50)
        self.assertEqual(tm.overloaded(1,2.0,"3"),"yellow submarine")
        self.assertAlmostEqual(tm.overloaded(1,2.0,3,4),6.0)
        self.assertEqual(tm.overloaded(1,2,"3"),9)

        self.assertEqual(tm.overload_1arg(1),None)
        self.assertAlmostEqual(tm.overload_1arg(1.0),2.0)


class TestManagedRef(TestCompile):
    header_file = '''
        struct A {
            int value;
            A(int value) : value(value) {}
            int get_value() const { return value; }
            void set_value(int v) { value = v; }
        };

        struct B {
            int othervalue;
            A a;
            B(int value) : othervalue(value), a(value * 2) {}
            ~B() { a.value = -1; }
            int get_value() const { return a.value; }
            A &get_a() { return a; }
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="A">
                <init/>
                <property name="value" get="get_value" set="set_value"/>
            </class>
            <class type="B">
                <init/>
                <attr cmember="a"/>
                <def func="get_value"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        b = tm.B(11)
        self.assertEqual(b.get_value(),22)
        a = b.a
        self.assertEqual(a.value,22)
        a.value = 23
        self.assertEqual(a.value,23)
        self.assertEqual(b.get_value(),23)
        a.__init__(24)
        self.assertEqual(a.value,24)
        self.assertEqual(b.get_value(),24)
        del b
        gc.collect()
        self.assertEqual(a.value,24)

class TestNumOperators(TestCompile):
    header_file = '''
        #include <math.h>

        class Vector {
        public:
            Vector() : x(0), y(0), z(0) {}
            Vector(float _x,float _y,float _z) : x(_x), y(_y), z(_z) {}

            bool operator==(const Vector &b) { return x == b.x && y == b.y && z == b.z; }
            bool operator!=(const Vector &b) { return !operator==(b); }

            Vector operator+(const Vector &b) const { return Vector(x+b.x,y+b.y,z+b.z); }
            Vector operator-(const Vector &b) const { return Vector(x-b.x,y-b.y,z-b.z); }

            Vector operator-() const { return Vector(-x,-y,-z); }

            Vector operator*(float c) const { return Vector(x*c,y*c,z*c); }
            Vector operator/(float c) const { return Vector(x/c,y/c,z/c); }

            Vector &operator+=(const Vector &b) { x += b.x; y += b.y; z += b.z; return *this; }
            Vector &operator-=(const Vector &b) { x -= b.x; y -= b.y; z -= b.z; return *this; }

            Vector &operator*=(float c) { x *= c; y *= c; z *= c; return *this; }
            Vector &operator/=(float c) { x /= c; y /= c; z /= c; return *this; }

            Vector operator*(const Vector &b) const {
                return Vector(y * b.z - z * b.y,z * b.x - x * b.z,x * b.y - y * b.x);
            }
            Vector &operator*=(const Vector &b) {
                float oldX = x;
                x = y * b.z - z * b.y;
                float oldY = y;
                y = z * b.x - oldX * b.z;
                z = oldX * b.y - oldY * b.x;
            }

            Vector pow(float n) const { return Vector(::pow(x,n),::pow(y,n),::pow(z,n)); }
            Vector powmod(float n,float m) const {
                return Vector(fmod(::pow(x,n),m),fmod(::pow(y,n),m),fmod(::pow(z,n),m));
            }
            Vector &ipow(float n) {
                x = ::pow(x,n);
                y = ::pow(y,n);
                z = ::pow(z,n);
                return *this;
            }
            Vector &ipowmod(float n,float m) {
                x = fmod(::pow(x,n),m);
                y = fmod(::pow(y,n),m);
                z = fmod(::pow(z,n),m);
                return *this;
            }

            float square() const { return x*x + y*y + z*z; }
            float absolute() const { return sqrtf(square()); }

            float x,y,z;
        };

        inline Vector operator*(float c,const Vector& v) { return Vector(c*v.x,c*v.y,c*v.z); }
        inline Vector operator/(float c,const Vector& v) { return Vector(c/v.x,c/v.y,c/v.z); }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="Vector">
                <init/>
                <attr cmember="x"/>
                <attr cmember="y"/>
                <attr cmember="z"/>
                <def name="__add__" func="operator+"/>
                <def name="__iadd__" func="operator+="/>
                <def name="__sub__" func="operator-" arity="1"/>
                <def name="__isub__" func="operator-="/>
                <def name="__mul__" func="operator*"/>
                <def name="__imul__" func="operator*="/>
                <def name="__rmul__" func="::operator*"/>
                <def name="__pow__" func="pow"/>
                <def name="__pow__" func="powmod"/>
                <def name="__ipow__" func="ipow"/>
                <def name="__ipow__" func="ipowmod"/>
                <def name="__abs__" func="absolute"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        va = tm.Vector(1,2,3)
        self.assertEqual(va.x,1)
        self.assertEqual(va.y,2)
        self.assertEqual(va.z,3)

        # __add__
        vb = va + tm.Vector(5,6,7)
        self.assertEqual(vb.x,6)
        self.assertEqual(vb.y,8)
        self.assertEqual(vb.z,10)

        # __iadd__
        vb += tm.Vector(-4,1,9)
        self.assertEqual(vb.x,2)
        self.assertEqual(vb.y,9)
        self.assertEqual(vb.z,19)

        # __sub__
        vb = tm.Vector(8,12,14) - tm.Vector(0,3,14)
        self.assertEqual(vb.x,8)
        self.assertEqual(vb.y,9)
        self.assertEqual(vb.z,0)

        # __isub__
        vb -= tm.Vector(10,20,30)
        self.assertEqual(vb.x,-2)
        self.assertEqual(vb.y,-11)
        self.assertEqual(vb.z,-30)

        # __mul__
        vb = tm.Vector(5,4,3) * tm.Vector(3,18,-2)
        self.assertEqual(vb.x,-62)
        self.assertEqual(vb.y,19)
        self.assertEqual(vb.z,78)

        # __mul__ with different type
        vb = tm.Vector(9,11,15) * 5
        self.assertEqual(vb.x,45)
        self.assertEqual(vb.y,55)
        self.assertEqual(vb.z,75)

        # __rmul__
        vb = 3 * tm.Vector(6,2,10)
        self.assertEqual(vb.x,18)
        self.assertEqual(vb.y,6)
        self.assertEqual(vb.z,30)

        # __imul__
        vb *= tm.Vector(19,-20,-7)
        self.assertEqual(vb.x,558)
        self.assertEqual(vb.y,696)
        self.assertEqual(vb.z,-474)

        # __imul__ with different type
        vb *= 7
        self.assertEqual(vb.x,3906)
        self.assertEqual(vb.y,4872)
        self.assertEqual(vb.z,-3318)

        # __pow__
        vb = tm.Vector(3,9,2) ** 3
        self.assertEqual(vb.x,27)
        self.assertEqual(vb.y,729)
        self.assertEqual(vb.z,8)

        # __ipow__
        vb **= 2
        self.assertEqual(vb.x,729)
        self.assertEqual(vb.y,531441)
        self.assertEqual(vb.z,64)

        # __pow__
        vb = pow(tm.Vector(4,-2,5),4,11)
        self.assertEqual(vb.x,3)
        self.assertEqual(vb.y,5)
        self.assertEqual(vb.z,9)

        # __abs__
        self.assertAlmostEqual(abs(tm.Vector(-1,3,12)),12.409674,4)



if __name__ == '__main__':
    unittest.main()
