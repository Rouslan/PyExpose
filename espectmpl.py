# the template strings used by espec.py, put here to keep espec.py uncluttered

import string

init_check = '''
    if(!self->initialized) {{
        PyErr_SetString(PyExc_RuntimeError,not_init_msg);
        return {0};
    }}
'''

class IfElse:
    def __init__(self,iftrue,iffalse = '',format = False):
        self.iftrue = iftrue
        self.iffalse = iffalse
        self.format = format

    def __call__(self,val,args,kwds):
        r = self.iftrue if val else self.iffalse
        return r.format(*args,**kwds) if self.format else r

class ForEach:
    def __init__(self,pattern,join = ''):
        self.pattern = pattern
        self.join = join

    def __call__(self,val,args,kwds):
        return self.join.join(self.pattern.format(x,*args,**kwds) for x in val)

class WithCondFormatter(string.Formatter):
    """Allow conditional inclusion of parts of a format string."""
    def __init__(self,conds):
        super(WithCondFormatter,self).__init__()
        self.conds = conds

    def get_value(self,key,args,kwds):
        val = kwds[key] if isinstance(key,basestring) else args[key]
        cond = self.conds.get(key)
        if cond:
            return cond(val,args,kwds)
        return val

class FormatWithCond(object):
    def __init__(self,body,**conds):
        self.formatter = WithCondFormatter(conds)
        self.body = body

    def format(self,*args,**kwds):
        return self.formatter.vformat(self.body,args,kwds)

    def __setitem__(self,key,val):
        self.formatter.conds[key] = val

property_get = FormatWithCond('''
PyObject *obj_{cname}_get{name}({ctype} *self,void *closure) {{
{checkinit}
    try {{
        return {code};
    }} EXCEPT_HANDLERS(0)
}}
''',
checkinit = IfElse(init_check.format('0')))

property_set = FormatWithCond('''
int obj_{cname}_set{name}({ctype} *self,PyObject *value,void *closure) {{
    if(!value) {{
        PyErr_SetString(PyExc_TypeError,no_delete_msg);
        return -1;
    }}
{checkinit}
    try {{
        self->base.{cppfunc}({code});
    }} EXCEPT_HANDLERS(-1)

    return 0;
}}
''',
checkinit = IfElse(init_check.format(-1)))

destruct = '''
void obj_{name}_dealloc(obj_{name} *self) {{
    if(self->initialized) self->base.~{dname}();
    self->ob_type->tp_free(reinterpret_cast<PyObject*>(self));
}}
'''

getset_table = '''
PyGetSetDef obj_{name}_getset[] = {{
    {items},
    {{0}}
}};
'''

member_table = '''
PyMemberDef obj_{name}_members[] = {{
    {items},
    {{0}}
}};
'''

method_table = '''
PyMethodDef obj_{name}_methods[] = {{
    {items},
    {{0}}
}};
'''

internconstruct = FormatWithCond('''
    obj_{name}({args}) : base({argvals}) {{
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_{name}Type());
{checkinit}
    }}
''',
checkinit = IfElse('        initialized = true;'))

classdef_start = FormatWithCond('''
extern PyTypeObject {dynamic[0]}obj_{name}Type;
inline PyTypeObject *get_obj_{name}Type() {{ return {dynamic[1]}obj_{name}Type; }}

struct obj_{name} {{
    PyObject_HEAD
    bool initialized;
    {type} base;

    void *operator new(size_t s) {{
        void *ptr = PyMem_Malloc(s);
        if(!ptr) throw std::bad_alloc();
        return ptr;
    }}

    void operator delete(void *ptr) {{
        PyMem_Free(ptr);
    }}
''',
dynamic = IfElse(['*',''],['','&']))

classdef_end = '''
};
'''

classinit = '''
int obj_{name}_init(obj_{name} *self,PyObject *args,PyObject *kwds) {{
    if(UNLIKELY(!safe_to_call_init(get_obj_{name}Type(),reinterpret_cast<PyObject*>(self)))) return -1;
{initdestruct}
    try {{
{initcode}
    }} EXCEPT_HANDLERS(-1)
    self->initialized = true;
    return 0;
}}
'''

classtypedef = FormatWithCond('''
PyTypeObject obj_{name}Type = {{
    PyObject_HEAD_INIT(&obj__CommonMetaType)
    0,                         /* ob_size */
    "{module}.{name}", /* tp_name */
    sizeof(obj_{name}), /* tp_basicsize */
    0,                         /* tp_itemsize */
    {destructref}, /* tp_dealloc */
    0,                         /* tp_print */
    0,                         /* tp_getattr */
    0,                         /* tp_setattr */
    0,                         /* tp_compare */
    0,                         /* tp_repr */
    0,                         /* tp_as_number */
    0,                         /* tp_as_sequence */
    0,                         /* tp_as_mapping */
    0,                         /* tp_hash */
    0,                         /* tp_call */
    0,                         /* tp_str */
    0,                         /* tp_getattro */
    0,                         /* tp_setattro */
    0,                         /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE, /* tp_flags */
    {doc}, /* tp_doc */
    0,                         /* tp_traverse */
    0,                         /* tp_clear */
    0,                         /* tp_richcompare */
    0,                         /* tp_weaklistoffset */
    0,                         /* tp_iter */
    0,                         /* tp_iternext */
    {methodsref}, /* tp_methods */
    {membersref}, /* tp_members */
    {getsetref}, /* tp_getset */
    {base}, /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    reinterpret_cast<initproc>(&obj_{name}_init), /* tp_init */
    0,                         /* tp_alloc */
    0                          /* tp_new */
}};
''',
destructref = IfElse('reinterpret_cast<destructor>(&obj_{name}_dealloc)','0',True),
methodsref = IfElse('obj_{name}_methods','0',True),
membersref = IfElse('obj_{name}_members','0',True),
getsetref = IfElse('obj_{name}_getset','0',True))

class_dynamic_typedef = FormatWithCond('''
PyTypeObject *obj_{name}Type;

inline PyTypeObject *create_obj_{name}Type() {{
    PyObject *bases = PyTuple_New({baseslen});
    if(UNLIKELY(!bases)) return 0;
    {basesassign}

    PyObject *name = PyString_FromString("{module}.{name}");
    if(UNLIKELY(!name)) {{
        Py_DECREF(bases);
        return 0;
    }}
    PyObject *dict = PyDict_New();
    if(UNLIKELY(!dict)) {{
        Py_DECREF(bases);
        Py_DECREF(name);
        return 0;
    }}

    PyTypeObject *type = reinterpret_cast<PyTypeObject*>(
        PyObject_CallFunctionObjArgs(reinterpret_cast<PyObject*>(&obj__CommonMetaType),name,bases,dict,0));

    Py_DECREF(bases);
    Py_DECREF(name);
    Py_DECREF(dict);
    if(UNLIKELY(!type)) return 0;

    type->tp_basicsize = sizeof(obj_{name});
    type->tp_dictoffset = 0;
    type->tp_weaklistoffset = 0;
    {destructref}
    {doc}
    {methodsref}
    {membersref}
    {getsetref}
    type->tp_init = reinterpret_cast<initproc>(&obj_{name}_init);

    return type;
}}
''',
basesassign = ForEach('PyTuple_SET_ITEM(bases,{0[0]},reinterpret_cast<PyObject*>({0[1]}));','\n    '),
destructref = IfElse('type->tp_dealloc = reinterpret_cast<destructor>(&obj_{name}_dealloc);',format=True),
doc = IfElse('type->tp_doc = {doc};',format=True),
methodsref = IfElse('type->tp_methods = obj_{name}_methods;',format=True),
membersref = IfElse('type->tp_members = obj_{name}_members;',format=True),
getsetref = IfElse('type->tp_getset = obj_{name}_getset;',format=True))

gccxmlinput_start = '''
#include <Python.h>
#include <string>
{0}

namespace {1} {{
typedef bool type_bool;
typedef signed int type_sint;
typedef unsigned int type_uint;
typedef signed short type_sshort;
typedef unsigned short type_ushort;
typedef signed long type_slong;
typedef unsigned long type_ulong;

#ifdef HAVE_LONG_LONG
typedef signed PY_LONG_LONG type_slonglong;
typedef unsigned PY_LONG_LONG type_ulonglong;
#endif

typedef float type_float;
typedef double type_double;
typedef long double type_long_double;
typedef size_t type_size_t;
typedef signed char type_schar;
typedef unsigned char type_uchar;
typedef char type_char;
typedef wchar_t type_wchar_t;
typedef Py_UNICODE type_py_unicode;
typedef std::string type_stdstring;
typedef std::wstring type_stdwstring;
typedef void type_void;

'''

# the back-slashes will line up after the double curly braces are replaced with single curly braces
module_start = '''
#include <Python.h>
#include <structmember.h>
#include <exception>
#include <string>
#include <limits.h>
#include <assert.h>
{includes}
#include "{module}.h"

#define EXCEPT_HANDLERS(RET) catch(py_error_set&) {{                 \\
        return RET;                                                 \\
    }} catch(std::bad_alloc&) {{                                      \\
        PyErr_NoMemory();                                           \\
        return RET;                                                 \\
    }} catch(std::exception &e) {{                                    \\
        PyErr_SetString(PyExc_RuntimeError,e.what());               \\
        return RET;                                                 \\
    }} catch(...) {{                                                  \\
        PyErr_SetString(PyExc_RuntimeError,unspecified_err_msg);    \\
        return RET;                                                 \\
    }}


#pragma GCC visibility push(hidden)

const char *no_delete_msg = "This attribute cannot be deleted";
const char *not_init_msg = "This object has not been initialized. Its __init__ method must be called first.";
const char *unspecified_err_msg = "unspecified error";
const char *no_keywords_msg = "keyword arguments are not accepted";


struct get_arg {{
    PyObject *args, *kwds;
    unsigned int tcount, kcount;
    get_arg(PyObject *args,PyObject *kwds) : args(args), kwds(kwds), tcount(0), kcount(0) {{
        assert(args != 0 && PyTuple_Check(args));
        assert(kwds == 0 || PyDict_Check(kwds));
    }}

    PyObject *operator()(const char *name,bool required);
    void finished();
}};

PyObject *get_arg::operator()(const char *name,bool required) {{
    if(tcount < PyTuple_GET_SIZE(args)) {{
        PyObject *r = PyTuple_GET_ITEM(args,tcount++);
        if(UNLIKELY(name && kwds && PyDict_GetItemString(kwds,name))) {{
            PyErr_Format(PyExc_TypeError,"got multiple values for keyword argument \\"%s\\"",name);
            throw py_error_set();
        }}
        return r;
    }}
    if(name && kwds) {{
        PyObject *r = PyDict_GetItemString(kwds,name);
        if(r) {{
            ++kcount;
            return r;
        }}
    }}

    if(UNLIKELY(required)) {{
        if(name) PyErr_Format(PyExc_TypeError,"a value for keyword argument \\"%s\\" is required",name);
        else PyErr_Format(PyExc_TypeError,"a value for positional argument # %d is required",tcount);
        throw py_error_set();
    }}

    return 0;
}}

void get_arg::finished() {{
    // TODO: check for unused arguments
}}


long PyToLong(PyObject *po) {{
    long r = PyInt_AsLong(po);
    if(UNLIKELY(r == -1 && PyErr_Occurred())) throw py_error_set();
    return r;
}}

long PyToXInt(PyObject *po,long max,long min) {{
    long r = PyToLong(po);
    if(UNLIKELY(r > max || r < min)) {{
        if(min == 0 && r < 0) PyErr_SetString(PyExc_TypeError,"value cannot be negative");
        else PyErr_SetString(PyExc_OverflowError,"value is out of range");
        throw py_error_set();
    }}
    return r;
}}

short PyToShort(PyObject *po) {{
    return static_cast<short>(PyToXInt(po,SHRT_MAX,SHRT_MIN));
}}

unsigned short PyToUShort(PyObject *po) {{
    return static_cast<unsigned short>(PyToXInt(po,USHRT_MAX,0));
}}

unsigned long PyToULong(PyObject *po) {{
    unsigned long r = PyLong_AsUnsignedLong(po);
    if(UNLIKELY(PyErr_Occurred())) throw py_error_set();
    return r;
}}

/* Although the size of int is checked here, the code generated by expose.py
   assumes the size of int is the same as it was when gccxml was called, making
   this unsuitable to compile on a different platform than the one where gccxml
   was called. A future version may fix this. */
#if INT_MAX == LONG_MAX
    #define PyToInt(po) PyToLong(po)
    #define PyToUInt(po) PyToULong(po)
#elif INT_MAX == SHRT_MAX
    #define PyToInt(po) PyToShort(po)
    #define PyToUInt(po) PyToUShort(po)
#else
    int PyToInt(PyObject *po) {{
        return static_cast<int>(PyToXInt(po,INT_MAX,INT_MIN));
    }}

    unsigned int PyToUInt(PyObject *po) {{
        return static_cast<unsigned int>(PyToXInt(po,UINT_MAX,0));
    }}
#endif

#ifdef HAVE_LONG_LONG
    long long PyToLongLong(PyObject *po) {{
        long long r = PyLong_AsLongLong(po);
        if(UNLIKELY(PyErr_Occurred())) throw py_error_set();
        return r;
    }}

    unsigned long long PyToULongLong(PyObject *po) {{
        unsigned long long r = PyLong_AsUnsignedLongLong(po);
        if(UNLIKELY(PyErr_Occurred())) throw py_error_set();
        return r;
    }}
#endif

double PyToDouble(PyObject *po) {{
    double r = PyFloat_AsDouble(po);
    if(UNLIKELY(PyErr_Occurred())) throw py_error_set();
    return r;
}}

inline PyObject *StringToPy(const std::string &s) {{
    return PyString_FromStringAndSize(s.c_str(),s.size());
}}

void NoSuchOverload(PyObject *args) {{
    if(PyTuple_GET_SIZE(args)) {{
        unsigned int needed = PyTuple_GET_SIZE(args); // len(args) - 1 commas and a terminating NUL
        for(unsigned int i = 0; i < PyTuple_GET_SIZE(args); ++i) {{
            assert(PyTuple_GET_ITEM(args,i)->ob_type && PyTuple_GET_ITEM(args,i)->ob_type->tp_name);
            needed += strlen(PyTuple_GET_ITEM(args,i)->ob_type->tp_name);
        }}

        char *msg = new char[needed];
        char *cur = msg;

        for(unsigned int i = 0; i < PyTuple_GET_SIZE(args); ++i) {{
            if(i) *cur++ = ',';
            const char *other = PyTuple_GET_ITEM(args,i)->ob_type->tp_name;
            while(*other) *cur++ = *other++;
        }}
        *cur = 0;

        PyErr_Format(PyExc_TypeError,"no overload takes (%s)",msg);
        delete[] msg;
    }} else {{
        PyErr_SetString(PyExc_TypeError,"no overload takes 0 arguments");
    }}
    throw py_error_set();
}}


// A common metatype for our types, to distinguish from user-defined types
PyTypeObject obj__CommonMetaType = {{
    PyObject_HEAD_INIT(0)
    0,                         /* ob_size */
    "{module}._internal_metaclass", /* tp_name */
    PyType_Type.tp_basicsize,  /* tp_basicsize */
    0,                         /* tp_itemsize */
    0,                         /* tp_dealloc */
    0,                         /* tp_print */
    0,                         /* tp_getattr */
    0,                         /* tp_setattr */
    0,                         /* tp_compare */
    0,                         /* tp_repr */
    0,                         /* tp_as_number */
    0,                         /* tp_as_sequence */
    0,                         /* tp_as_mapping */
    0,                         /* tp_hash */
    0,                         /* tp_call */
    0,                         /* tp_str */
    0,                         /* tp_getattro */
    0,                         /* tp_setattro */
    0,                         /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE, /* tp_flags */
    0,                         /* tp_doc */
    0,                         /* tp_traverse */
    0,                         /* tp_clear */
    0,                         /* tp_richcompare */
    0,                         /* tp_weaklistoffset */
    0,                         /* tp_iter */
    0,                         /* tp_iternext */
    0,                         /* tp_methods */
    0,                         /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,                         /* tp_init */
    0,                         /* tp_alloc */
    0                          /* tp_new */
}};

/* Check if __init__ is being called on the wrong type.

   Python already prevents methods from being used on objects that don't derive
   from the appropriate class, so it is assumed that self->tp_mro contains our
   type. */
bool safe_to_call_init(PyTypeObject *target,PyObject *self) {{
    assert(self->ob_type && self->ob_type->tp_mro);
    PyObject *mro = self->ob_type->tp_mro;
    for(unsigned int i = 0; reinterpret_cast<PyTypeObject*>(PyTuple_GET_ITEM(mro,i)) != target; ++i) {{
        assert(i < PyTuple_GET_SIZE(mro));
        if(UNLIKELY(PyTuple_GET_ITEM(mro,i)->ob_type == &obj__CommonMetaType)) {{
            PyErr_SetString(PyExc_TypeError,"__init__ cannot be used directly on a derived type");
            return false;
        }}
    }}
    return true;
}}


struct obj__Common {{
    PyObject_HEAD
    bool initialized;
}};

/* trying to inherit from more than one type raises a TypeError if there isn't a
   common base */
PyTypeObject obj__CommonType = {{
    PyObject_HEAD_INIT(&obj__CommonMetaType)
    0,                         /* ob_size */
    "{module}._internal_class", /* tp_name */
    sizeof(obj__Common),       /* tp_basicsize */
    0,                         /* tp_itemsize */
    0,                         /* tp_dealloc */
    0,                         /* tp_print */
    0,                         /* tp_getattr */
    0,                         /* tp_setattr */
    0,                         /* tp_compare */
    0,                         /* tp_repr */
    0,                         /* tp_as_number */
    0,                         /* tp_as_sequence */
    0,                         /* tp_as_mapping */
    0,                         /* tp_hash */
    0,                         /* tp_call */
    0,                         /* tp_str */
    0,                         /* tp_getattro */
    0,                         /* tp_setattro */
    0,                         /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE, /* tp_flags */
    0,                         /* tp_doc */
    0,                         /* tp_traverse */
    0,                         /* tp_clear */
    0,                         /* tp_richcompare */
    0,                         /* tp_weaklistoffset */
    0,                         /* tp_iter */
    0,                         /* tp_iternext */
    0,                         /* tp_methods */
    0,                         /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,                         /* tp_init */
    0,                         /* tp_alloc */
    0                          /* tp_new */
}};
'''


module_init = FormatWithCond('''
PyMethodDef func_table[] = {{
{funclist}
    {{0}}
}};


extern "C" SHARED(void) init{module}(void) {{
    obj__CommonMetaType.tp_base = &PyType_Type;
    if(UNLIKELY(PyType_Ready(&obj__CommonMetaType) < 0)) return;

    if(UNLIKELY(PyType_Ready(&obj__CommonType) < 0)) return;''',
funclist = ForEach('    {0},','\n'))

module_class_prepare = FormatWithCond('''
    {base}
    obj_{name}Type.tp_new = &PyType_GenericNew;
    if(UNLIKELY(PyType_Ready(&obj_{name}Type) < 0)) return;
''',
base = IfElse('obj_{name}Type.tp_base = get_obj_{base}Type();',format=True))

module_create = '''
    PyObject *m = Py_InitModule3("{name}",func_table,{doc});
    if(UNLIKELY(!m)) return;

    Py_INCREF(&obj__CommonMetaType);
    PyModule_AddObject(m,"_internal_metaclass",reinterpret_cast<PyObject*>(&obj__CommonMetaType));

    Py_INCREF(&obj__CommonType);
    PyModule_AddObject(m,"_internal_class",reinterpret_cast<PyObject*>(&obj__CommonType));
'''

module_class_add = '''
    Py_INCREF(&obj_{0}Type);
    PyModule_AddObject(m,"{0}",reinterpret_cast<PyObject*>(&obj_{0}Type));
'''

module_dynamic_class_add = '''
    obj_{0}Type = create_obj_{0}Type();
    if(UNLIKELY(!obj_{0}Type)) return;
    PyModule_AddObject(m,"{0}",reinterpret_cast<PyObject*>(obj_{0}Type));
'''

module_end = '''
}

#pragma GCC visibility pop
'''

get_base = FormatWithCond('''
inline {type} &get_base_{name}(PyObject *o) {{
    if(UNLIKELY(!PyObject_TypeCheck(o,get_obj_{name}Type()))) {{
        PyErr_SetString(PyExc_TypeError,"object is not an instance of {name}");
        throw py_error_set();
    }}
    return reinterpret_cast<obj_{name}*>(o)->base;
}}
''',
typecast = IfElse(
    '*dynamic_cast<{type}*>(static_cast<void*>(&reinterpret_cast<obj_{name}*>(o)->base))',
    'reinterpret_cast<obj_{name}*>(o)->base',
    True))

header_start = '''
#pragma once
#ifndef {module}_h
#define {module}_h

#ifdef __GNUC__
    #define LIKELY(X) __builtin_expect(static_cast<bool>(X),1)
    #define UNLIKELY(X) __builtin_expect(static_cast<bool>(X),0)
#else
    #define LIKELY(X) X
    #define UNLIKELY(X) X
#endif

#if defined(_WIN32) || defined(__CYGWIN__) || defined(__BEOS__)
    #define SHARED(RET) __declspec(dllexport) RET
#elif defined(__GNUC__) && __GNUC__ >= 4
    #define SHARED(RET) RET __attribute__((visibility("default")))
#else
    #define SHARED(RET) RET
#endif


#pragma GCC visibility push(hidden)

/* when thrown, indicates that a PyErr_X function was already called with the
   details of the exception. As such, it carries no information of its own. */
struct py_error_set {{}};

'''

header_end = '''
#pragma GCC visibility pop

#endif
'''

overload_func_call = FormatWithCond('''
{nokwdscheck}
{inner}

        NoSuchOverload(args);
end:    ;
''',
nokwdscheck = IfElse('''
        if(kwds && PyDict_Size(kwds)) {
            PyErr_SetString(PyExc_TypeError,no_keywords_msg);
            throw py_error_set();
        }
'''))

typecheck_start = '''
{type} &get_base_{name}(PyObject *x,bool safe = true) {{
'''

# The
# reinterpret_cast<long>(static_cast<{type}*>(reinterpret_cast<{othertype}*>(1))) != 1
# part is added as an optimization trick. The purpose of the following code is
# to get a reference to the correct location in memory. Because {othertype}
# derives from more than one type, the memory for {type} wont necessarily be at
# the beginning of {othertype}. If however, {type} does occur at the beginning,
# this added part will evaluate to false and the rest of the expression should
# be subject to dead code removal by the compiler.
typecheck_test = '''
    if(reinterpret_cast<long>(static_cast<{type}*>(reinterpret_cast<{othertype}*>(1))) != 1 && PyObject_IsInstance(x,reinterpret_cast<PyObject*>(get_obj_{other}Type())))
        return reinterpret_cast<obj_{other}*>(x)->base;
'''

typecheck_else = '''
    if(UNLIKELY(safe && !PyObject_IsInstance(x,reinterpret_cast<PyObject*>(get_obj_{name}Type())))) {{
        PyErr_SetString(PyExc_TypeError,"object is not an instance of {name}");
        throw py_error_set();
    }}
    assert(PyObject_IsInstance(x,reinterpret_cast<PyObject*>(get_obj_{name}Type())));
    return reinterpret_cast<obj_{name}*>(x)->base;
}}
'''

function = '''
PyObject *{funcnameprefix}{name}({selfvar}{args}) {{
{prolog}
    try {{
        {code}
    }} EXCEPT_HANDLERS(0)
}}
'''
