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
import weakref


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,PROJECT_DIR)


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

    cpp_file = None

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

    templates = False

    @classmethod
    def modname(cls):
        return cls.__name__.lower()

    def setUp(self):
        self.olddir = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)

        try:
            pyinc = distutils.sysconfig.get_python_inc()

            write_file('main.h',self.header_file)
            write_file('spec.xml',self.spec_file)
            if self.cpp_file:
                write_file('main.cpp',self.cpp_file)

            gccxml_flags = '-I{0} -I{1}'.format(pyinc,PROJECT_DIR)
            if self.templates:
                gccxml_flags += ' -DPYEXPOSE_TEMPLATE_HELPERS=1'

            spec = espec.getspec('spec.xml')
            spec.name = self.modname() # give the new module a unique name
            expose.generate_intermediate(spec,'gccxml.interm','.',None,'g++',gccxml_flags)
            expose.generate_module(spec,'gccxml.interm','.')

            self.comp = distutils.ccompiler.new_compiler()
            distutils.sysconfig.customize_compiler(self.comp)
            self.comp.add_include_dir(pyinc)
            self.comp.add_include_dir(PROJECT_DIR)
            self.comp.add_library('stdc++')

            if self.templates:
                self.comp.define_macro('PYEXPOSE_TEMPLATE_HELPERS',1)

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
            files = [self.modname() + '.cpp']
            if self.cpp_file:
                files.append('main.cpp')
            obj = self.comp.compile(files,debug=True)
            self.comp.link_shared_lib(obj,self.modname(),debug=True)
        except (distutils.ccompiler.CompileError,distutils.ccompiler.LinkError) as e:
            self.fail(str(e))

        mname = self.comp.library_filename(self.modname(),'shared')
        name,ext = os.path.splitext(mname)
        if name != self.modname():
            os.rename(mname,self.modname()+ext)

        return __import__(self.modname())

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
                <def name="__sequence__getitem__" func="at" return-semantic="copy"/>
                <def name="__sequence__setitem__" assign-to="at"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        v = tm.DVector()
        self.assertEqual(v.size,0)
        v.push_back(3)
        self.assertEqual(v.size,1)
        self.assertEqual(v[0],3)


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
                return *this;
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


class TestConstructorDestructor(TestCompile):
    header_file = '''
        int thingcount = 0;

        struct A {
            A() { ++thingcount; }
            ~A() { --thingcount; }
        };

        struct B {
            B() { ++thingcount; }
            ~B() { --thingcount; }
        };

        int count() { return thingcount; }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="A" new-initializes="false"/>
            <class type="B" new-initializes="true"/>
            <def func="count"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        self.assertEqual(tm.count(),0)
        a = tm.A()
        self.assertEqual(tm.count(),1)
        b = tm.B()
        self.assertEqual(tm.count(),2)
        del a
        del b
        gc.collect()
        self.assertEqual(tm.count(),0)


        # create subclasses that don't call the base class' __init__ method

        class A2(tm.A):
            def __init__(self):
                pass

        class B2(tm.B):
            def __init__(self):
                pass


        a = A2()
        self.assertEqual(tm.count(),0)
        b = B2()
        self.assertEqual(tm.count(),1)
        super(A2,a).__init__()
        self.assertEqual(tm.count(),2)
        super(B2,b).__init__()
        self.assertEqual(tm.count(),2)
        del a
        del b
        gc.collect()
        self.assertEqual(tm.count(),0)
        a = A2()
        self.assertEqual(tm.count(),0)
        del a
        gc.collect()
        self.assertEqual(tm.count(),0)


class TestVirtualMethod(TestCompile):
    header_file = '''
        struct Thing {
            virtual double factor1() const { return 22; }
            virtual double factor2() const { return 33; }
            double getval1() const { return factor1() * 2; }
            double getval2() const { return factor2() * 3; }

            virtual int add(int a,int b) { return a + b; }
            int add56() { return add(5,6); }
        };

        struct Thing2 {
            virtual void get_str() = 0;
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="Thing">
                <def func="factor1"/>
                <def func="getval1"/>
                <def func="factor2" bridge-virtual="false"/>
                <def func="getval2"/>
                <def func="add"/>
                <def func="add56"/>
            </class>
            <class type="Thing2">
                <def func="get_str"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        thing = tm.Thing()
        self.assertEqual(thing.getval1(),44)

        class SubClass(tm.Thing):
            def factor1(self):
                return 3
            def factor2(self):
                return 5
            def add(self,a,b):
                return a * b

        thing = SubClass()
        self.assertEqual(thing.getval1(),6)
        self.assertEqual(thing.getval2(),99)
        self.assertEqual(thing.add56(),30)

        thing = tm.Thing2()
        self.assertRaises(NotImplementedError,thing.get_str)


class TestConversion(TestCompile):
    header_file = '''
        class borrowed_ref {
            friend class object;
            PyObject *_ptr;
        public:
            explicit borrowed_ref(PyObject *ptr) : _ptr(ptr) {}
        };

        class new_ref {
            friend class object;
            PyObject *_ptr;
        public:
            explicit new_ref(PyObject *ptr) : _ptr(ptr) {}
        };

        class object {
            PyObject *_ptr;
        public:
            object(borrowed_ref r) : _ptr(r._ptr) { Py_INCREF(_ptr); }
            object(new_ref r) : _ptr(r._ptr) {}
            object(const object &b) : _ptr(b._ptr) { Py_INCREF(_ptr); }
            ~object() {
                Py_DECREF(_ptr);
            }

            object &operator=(const object &b) {
                Py_INCREF(b._ptr);
                Py_DECREF(_ptr);
                _ptr = b._ptr;
                return *this;
            }

            PyObject *ptr() const { return _ptr; }
            PyObject *get_new_ref() const {
                Py_INCREF(_ptr);
                return _ptr;
            }
        };

        object dup_tuple(object o) {
            return new_ref(PyTuple_Pack(2,o.ptr(),o.ptr()));
        }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <from-pyobject type="object">object(borrowed_ref(<val/>))</from-pyobject>
            <to-pyobject type="object"><val/>.get_new_ref()</to-pyobject>
            <def func="dup_tuple"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        self.assertEqual(tm.dup_tuple('kitty'),('kitty','kitty'))


class TestTemplateAssoc(TestCompile):
    header_file = TestConversion.header_file + '''
        template<typename T> PyTypeObject *get_type();
        template<typename T> T &get_base(PyObject *o);

        template<typename T> class pyptr : public object {
        public:
            template<typename REF> pyptr(REF r) : object(r) { assert(PyObject_IsInstance(ptr(),get_type<T>())); }
            pyptr(const pyptr<T> &b) : object(b) {}

            T &base() {
                return get_base<T>(ptr());
            }
            const T &base() const {
                return get_base<T>(ptr());
            }
            T &operator*() { return base(); }
            const T &operator*() const { return base(); }
            T *operator->() { return &base(); }
            const T *operator->() const { return &base(); }
        };

        class Thing {
        public:
            int func() { return 6; }
        };

        int getval(pyptr<Thing> t) {
            return t->func();
        }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <from-pyobject type="pyptr&lt;Thing&gt;">pyptr&lt;Thing&gt;(borrowed_ref(<val/>))</from-pyobject>
            <to-pyobject type="pyptr&lt;Thing&gt;"><val/>.get_new_ref()</to-pyobject>
            <class type="Thing"/>
            <def func="getval"/>
        </module>
    '''

    templates = True

    def runTest(self):
        tm = self.compile()
        self.assertEqual(tm.getval(tm.Thing()),6)


class TestSmartPtr(TestCompile):
    header_file = header_file = TestConversion.header_file + '''
        template<typename T> PyTypeObject *get_type();
        template<typename T> T &get_base(PyObject *o);

        template<typename T> class pyptr {
            object _obj;
            T *_ptr;
        public:
            template<typename REF> pyptr(T *ptr,REF r) : _obj(r), _ptr(ptr) {}
            pyptr(const pyptr<T> &b) : _obj(b._obj), _ptr(b._ptr) {}
            pyptr() : _obj(Py_None), _ptr(0) {}

            T &operator*() { return *_ptr; }
            const T &operator*() const { *_ptr; }
            T *operator->() { return _ptr; }
            const T *operator->() const { return _ptr; }

            object obj() { return _obj; }
        };

        class Thing {
        public:
            int func() { return 6; }
        };

        int getval(pyptr<Thing> t) {
            return t->func();
        }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <smart-ptr>
                <ptr-type>pyptr&lt;<type/>&gt;</ptr-type>
                <from-pyobject>pyptr&lt;<type/>&gt;(&amp;<val/>,borrowed_ref(<pyobject/>))</from-pyobject>
                <to-pyobject><val/>.obj().get_new_ref()</to-pyobject>
            </smart-ptr>
            <class type="Thing"/>
            <def func="getval"/>
        </module>
    '''

    templates = True

    def runTest(self):
        tm = self.compile()
        self.assertEqual(tm.getval(tm.Thing()),6)


class TestSubscriptAttr(TestCompile):
    header_file = '''
        struct Nest1 {
            int item;
        };
        union Nest2 {
            Nest1 items[2];
            double stuff;
        };
        class Thing {
        public:
            int items[3];
            Nest2 nest;

            int getitem(int index) {
                // since this is just a test, we wont bother making sure that index is between 0 and 2
                return items[index];
            }
            int getnested() {
                return nest.items[1].item;
            }
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="Thing">
                <def func="getitem"/>
                <def func="getnested"/>
                <attr name="a" cmember="items[0]"/>
                <attr name="b" cmember="items[2]"/>
                <attr name="c" cmember="nest.items[1].item"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        thing = tm.Thing()
        thing.a = 12
        thing.b = 30
        thing.c = -2
        self.assertEqual(thing.getitem(0),12)
        self.assertEqual(thing.getitem(2),30)
        self.assertEqual(thing.getnested(),-2)


class TestNoInit(TestCompile):
    header_file = '''
        struct Abstract {
            virtual int method() = 0;
        };

        struct Concrete : Abstract {
            int method() { return 5; }
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="Abstract">
                <no-init/>
                <def func="method" bridge-virtual="false"/>
            </class>
            <class type="Concrete"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        self.assertRaises(TypeError,tm.Abstract)
        c = tm.Concrete()
        self.assertEqual(c.method(),5)
        self.assertRaises(NotImplementedError,tm.Abstract.method,c)


class TestOverloadedNew(TestCompile):
    header_file = '''
        #include <Python.h>

        struct X {};

        PyObject *create_x();
    '''

    cpp_file = '''
        #include <new>

        #include "main.h"
        #include "testoverloadednew.h"

        PyObject *create_x() {
            return reinterpret_cast<PyObject*>(new obj_X());
        }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="X"/>
            <def func="create_x"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()
        x = tm.create_x()
        gc.collect()


class TestInvariableStorage(TestCompile):
    header_file = '''
        struct A {
            int value;
        };

        struct B {
            int othervalue;
            A a;
        };

        bool a_is_invariable();
        bool b_is_invariable();

        /* invariable_storage is not declared until the extension code is
           generated */
        #ifndef __GCCXML__
        #include "testinvariablestorage.h"
        bool a_is_invariable() { return invariable_storage<A>::value == 1; }
        bool b_is_invariable() { return invariable_storage<B>::value == 1; }
        #endif
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="A"/>
            <class type="B">
                <attr cmember="a"/>
            </class>
            <def func="a_is_invariable"/>
            <def func="b_is_invariable"/>
        </module>
    '''

    templates = True

    def runTest(self):
        tm = self.compile()

        self.assertEqual(tm.a_is_invariable(),False)
        self.assertEqual(tm.b_is_invariable(),True)


class TestVars(TestCompile):
    header_file = '''
        const double var2 = 24.0;

        #define var3 "36"

        struct Thing {
            int var;
            Thing() : var(48) {}
        };

        Thing var4;
        Thing &var5 = var4;

        int get_var4_int() { return var4.var; }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <var name="var1" value="12"/>
            <var value="var2"/>
            <var value="var3"/>
            <var value="var4"/>
            <var value="var5" ref="false"/>

            <class type="Thing">
                <attr cmember="var"/>
            </class>
            <def func="get_var4_int"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        self.assertEqual(tm.var1,12)
        self.assertEqual(tm.var2,24.0)
        self.assertEqual(tm.var3,"36")
        self.assertEqual(tm.var4.var,48)
        self.assertEqual(tm.var5.var,48)

        tm.var4.var += 1
        tm.var5.var -= 1

        self.assertEqual(tm.var4.var,49)
        self.assertEqual(tm.var5.var,47)

        self.assertEqual(tm.get_var4_int(),49);


class TestWeakListAndDict(TestCompile):
    header_file = '''
        struct ThingA {};
        struct ThingB {};
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="ThingA" weakrefs="false"/>
            <class type="ThingB" instance-dict="false"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        a = tm.ThingA()
        b = tm.ThingB()

        wb = weakref.ref(b)
        self.assertRaises(TypeError,weakref.ref,a)

        def battr(x):
            x.hamburger = 'fluffy'

        battr(a)
        self.assertRaises(AttributeError,battr,b)


class TestObjectH(TestCompile):
    header_file = '''
        #include "pyobject.h"

        void swap_1_and_3(py::object list) {
            py::object temp = list[1];
            list[1] = list[3];
            list[3] = temp;
        }
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <def func="swap_1_and_3"/>
        </module>
    '''

    templates = True

    def runTest(self):
        tm = self.compile()

        x = ['alpha','beta','gamma','delta']
        tm.swap_1_and_3(x)
        self.assertEqual(x,['alpha','delta','gamma','beta'])


class TestGC(TestCompile):
    header_file = '''
        class WithGC {
        public:
            PyObject *member;
            WithGC() : member(0) {}
            ~WithGC() {
                Py_XDECREF(member);
            }
        };

        class Without {
        public:
            PyObject *member;
            Without() : member(0) {}
            ~Without() {
                Py_XDECREF(member);
            }
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="WithGC" use-gc="true">
                <attr cmember="member"/>
            </class>
            <class type="Without" use-gc="false">
                <attr cmember="member"/>
            </class>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        # with GC support
        w = tm.WithGC()
        w2 = tm.WithGC()

        wr = weakref.ref(w)
        w2r = weakref.ref(w2)

        w.member = w2
        w2.member = w

        self.assertTrue(wr())
        self.assertTrue(w2r())

        w = None
        w2 = None

        gc.collect()

        self.assertTrue(wr() is None)
        self.assertTrue(w2r() is None)


        # without GC support
        wo = tm.Without()
        wo2 = tm.Without()

        wor = weakref.ref(wo)
        wo2r = weakref.ref(wo2)

        wo.member = wo2
        wo2.member = wo

        self.assertTrue(wor())
        self.assertTrue(wo2r())

        wo = None
        wo2 = None

        gc.collect()

        # a reference cycle should prevent these from being destroyed
        self.assertTrue(wor())
        self.assertTrue(wo2r())

        # now break the cycle
        wo2 = wo2r()
        wor().member = None
        wo2.member = None
        wo2 = None

        gc.collect()

        self.assertTrue(wr() is None)
        self.assertTrue(w2r() is None)


class TestGCHandler(TestCompile):
    header_file = '''
        // a class with special handling
        class Thing {
            PyObject *obj;
        public:
            void clear() { Py_CLEAR(obj); }

            Thing() : obj(NULL) {}
            ~Thing() { clear(); }

            PyObject *get_obj() {
                Py_XINCREF(obj);
                return obj;
            }
            void set_obj(PyObject *_obj) {
                PyObject *tmp = obj;
                obj = _obj;
                Py_XINCREF(obj);
                Py_XDECREF(tmp);
            }

            PyObject *raw_obj() { return obj; }
        };

        // a class that has a member with special handling
        class Container {
        public:
            Thing obj;

            PyObject *get_obj() { return obj.get_obj(); }
            void set_obj(PyObject *_obj) { obj.set_obj(_obj); }
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="Thing" use-gc="true">
                <property name="member" get="get_obj" set="set_obj"/>
            </class>
            <class type="Container" use-gc="true">
                <property name="member" get="get_obj" set="set_obj"/>
            </class>
            <gc-handler type="Thing">
                <traverse>Py_VISIT(<val/>.raw_obj());</traverse>
                <clear><val/>.clear();</clear>
            </gc-handler>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        for C in [tm.Thing,tm.Container]:
            obj = C()
            obj2 = C()

            objr = weakref.ref(obj)
            obj2r = weakref.ref(obj2)

            obj.member = obj2
            obj2.member = obj

            self.assertTrue(objr())
            self.assertTrue(obj2r())

            obj = None
            obj2 = None

            gc.collect()

            self.assertTrue(objr() is None)
            self.assertTrue(obj2r() is None)


class TestAlternateSpec(TestCompile):
    header_file = '''
        class Thing {
            PyObject *obj;
        public:
            void clear() { Py_CLEAR(obj); }

            Thing() : obj(NULL) {}
            Thing(const Thing &b) : obj(b.get_obj()) {}
            ~Thing() { clear(); }

            PyObject *get_obj() const {
                Py_XINCREF(obj);
                return obj;
            }
            void set_obj(PyObject *_obj) {
                PyObject *tmp = obj;
                obj = _obj;
                Py_XINCREF(obj);
                Py_XDECREF(tmp);
            }

            int __py_traverse__(visitproc visit,void *arg) { return obj ? visit(obj,arg) : 0; }
            void __py_clear__() { clear(); }
            PyObject *__py_to_pyobject__() { return get_obj(); }
            static Thing __py_from_pyobject__(PyObject *val) {
                Thing t;
                t.set_obj(val);
                return t;
            }
        };

        class Container {
        public:
            Thing obj;
        };
    '''

    spec_file = '''<?xml version="1.0"?>
        <module name="testmodule" include="main.h">
            <class type="Thing" use-gc="true">
                <property name="member" get="get_obj" set="set_obj"/>
            </class>
            <class type="Container" use-gc="true"/>
        </module>
    '''

    def runTest(self):
        tm = self.compile()

        for C in [tm.Thing,tm.Container]:
            obj = C()
            obj2 = C()

            objr = weakref.ref(obj)
            obj2r = weakref.ref(obj2)

            obj.member = obj2
            obj2.member = obj

            self.assertTrue(objr())
            self.assertTrue(obj2r())

            obj = None
            obj2 = None

            gc.collect()

            self.assertTrue(objr() is None)
            self.assertTrue(obj2r() is None)


if __name__ == '__main__':
    unittest.main()
