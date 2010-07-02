# the template strings used by espec.py, put here to keep espec.py uncluttered

import jinja2


def quote_c(x):
    # python's non-unicode string syntax appears to be the same as C's
    return '"'+x.encode('utf_8').encode('string_escape')+'"'

env = jinja2.Environment(
    block_start_string = '<@',
    block_end_string = '@>',
    variable_start_string = '<%',
    variable_end_string = '%>',
    comment_start_string = '<#',
    comment_end_string = '#>',
    line_statement_prefix = '==',
    line_comment_prefix = '=#',
    autoescape = False)

env.filters['quote'] = quote_c



property_get = env.from_string('''
PyObject *obj_<% cname %>_get<% name %>(obj_<% cname %> *self,void *) {
<% prolog %>
    try {
<% code %>
    } EXCEPT_HANDLERS(0)
}
''')

property_set = env.from_string('''
int obj_<% cname %>_set<% name %>(obj_<% cname %> *self,PyObject *arg,void *) {
    if(!arg) {
        PyErr_SetString(PyExc_TypeError,no_delete_msg);
        return -1;
    }
<% prolog %>
    try {
<% code %>
    } EXCEPT_HANDLERS(-1)
}
''')

property_table = env.from_string('''<@
    macro _ter(x,what) @><@
        if x @>reinterpret_cast<<% what %>ter>(&obj_<% cname %>_<% what %><% name %>)<@
        else @>0<@ endif @><@
    endmacro @>{const_cast<char*>("<% name %>"),<% _ter(get,"get") %>,<% _ter(set,"set") %>,<@ if doc @>const_cast<char*>(<% doc|quote %>)<@ else @>0<@ endif @>,0}''')

destruct = env.from_string('''
void obj_<% name %>_dealloc(obj_<% name %> *self) {
== if features.managed_ref or (destructor and not new_init)
    switch(self->mode) {

== if features.managed_ref
    case MANAGEDREF:
        reinterpret_cast<ref_<% name %>*>(self)->~ref_<% name %>();
        break;
== endif
    case CONTAINS:
        self->base.<% destructor %>();
        break;
    default: // suppress warnings
        break;
    }
== elif destructor
    self->base.<% destructor %>();
== endif
    self->ob_type->tp_free(reinterpret_cast<PyObject*>(self));
}
''')

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

classdef = env.from_string('''
extern PyTypeObject <% '*' if dynamic %>obj_<% name %>Type;
inline PyTypeObject *get_obj_<% name %>Type() { return <% '&' if not dynamic %>obj_<% name %>Type; }

== if canholdref
struct ref_<% name %> {
    PyObject_HEAD
    storage_mode mode;
    <% type %> &base;
    PyObject *container;

    PY_MEM_NEW_DELETE

    ref_<% name %>(<% type %> &base,PyObject *container) : mode(MANAGEDREF), base(base), container(container) {
        Py_INCREF(container);
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_<% name %>Type());
    }

    ~ref_<% name %>() {
        Py_DECREF(container);
    }
};
== endif

struct obj_<% name %> {
    PyObject_HEAD
== if canholdref or not new_init
    storage_mode mode;
== endif
    <% type %> base;

    PY_MEM_NEW_DELETE

== for con in constructors
    obj_<% name %>(<% con.args %>) : base(<% con.argvals %>) {
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_<% name %>Type());
==     if canholdref or not new_init
        mode = CONTAINS;
==     endif
    }
== endfor
};
== if template_assoc

template<> inline PyTypeObject *get_type<<% original_type %> >() {
    return get_obj_<% name %>Type();
}

==     if canholdref or not new_init
<% original_type %> &cast_base_<% name %>(PyObject *o);
template<> inline <% original_type %> &cast_base<<% original_type %> >(PyObject *o) {
    return cast_base_<% name %>(o);
}
==     else
template<> inline <% original_type %> &cast_base<<% original_type %> >(PyObject *o) {
    return reinterpret_cast<obj_<% name %>*>(o)->base;
}
==     endif

<% original_type %> &get_base_<% name %>(PyObject *o<% ',bool safe=true' if bool_arg_get %>);
template<> inline <% original_type %> &get_base<<% original_type %> >(PyObject *o) {
    return get_base_<% name %>(o);
}
== endif
''')


classtypedef = env.from_string('''
<@ macro objsize() @><@
    if features.managed_ref
        @>sizeof(ref_<% name %>) > sizeof(obj_<% name %>) ? sizeof(ref_<% name %>) : <@
    endif
    @>sizeof(obj_<% name %>)<@ endmacro @>

int obj_<% name %>_init(obj_<% name %> *self,PyObject *args,PyObject *kwds) {
== if derived
    if(UNLIKELY(
==     for d in derived
        <@ if not loop.first @>|| <@ endif @>PyObject_IsInstance(reinterpret_cast<PyObject*>(self),reinterpret_cast<PyObject*>(get_obj_<% d %>Type()))
==     endfor
    )) {
        PyErr_SetString(PyExc_TypeError,init_on_derived_msg);
        return -1;
    }
== endif

    <% type %> *addr = &self->base;

== if features.managed_ref or (destructor and not new_init)
    /* before we can call the constructor, the destructor needs to be called if
       we already have an initialized object */
    switch(self->mode) {
==     if features.managed_ref
    case MANAGEDREF:
==         if destructor
        reinterpret_cast<ref_<% name %>*>(self)->base.<% destructor %>();
==         endif
        addr = &reinterpret_cast<ref_<% name %>*>(self)->base;
        break;
==     endif
==     if destructor
    case CONTAINS:
        self->base.<% destructor %>();
        break;
==     endif
    default:
        self->mode = CONTAINS;
        break;
    }
== elif destructor
    self->base.<% destructor %>();
== elif not new_init
    self->mode = CONTAINS;
== endif
    try {
<% initcode %>
    } EXCEPT_HANDLERS(-1)
}

== if new_init
PyObject *obj_<% name %>_new(PyTypeObject *type,PyObject *,PyObject *) {
    try {
        obj_<% name %> *ptr = reinterpret_cast<obj_<% name %>*>(type->tp_alloc(type,0));
        if(ptr) new(&ptr->base) <% type %>();
        return reinterpret_cast<PyObject*>(ptr);
    } EXCEPT_HANDLERS(0)
}
== endif

== if dynamic
PyTypeObject *obj_<% name %>Type;

inline PyTypeObject *create_obj_<% name %>Type() {
    PyObject *bases = PyTuple_New(<% bases|length %>);
    if(UNLIKELY(!bases)) return 0;
==     for base in bases
    PyTuple_SET_ITEM(bases,<% loop.index0 %>,reinterpret_cast<PyObject*>(<% base %>));
==     endfor

    PyObject *name = PyString_FromString("<% module %>.<% name %>");
    if(UNLIKELY(!name)) {
        Py_DECREF(bases);
        return 0;
    }
    PyObject *dict = PyDict_New();
    if(UNLIKELY(!dict)) {
        Py_DECREF(bases);
        Py_DECREF(name);
        return 0;
    }

    PyTypeObject *type = reinterpret_cast<PyTypeObject*>(
        PyObject_CallFunctionObjArgs(reinterpret_cast<PyObject*>(&PyType_Type),name,bases,dict,0));

    Py_DECREF(bases);
    Py_DECREF(name);
    Py_DECREF(dict);
    if(UNLIKELY(!type)) return 0;

    type->tp_basicsize = <% objsize() %>;
    type->tp_flags |= Py_TPFLAGS_CHECKTYPES;
    type->tp_dictoffset = 0;
    type->tp_weaklistoffset = 0;
<@ if destructref @>    type->tp_dealloc = reinterpret_cast<destructor>(&obj_<% name %>_dealloc);<@ endif @>
<@ if '__cmp__' in specialmethods @>    type->tp_compare = reinterpret_cast<cmpfunc>(&obj_<% name %>___cmp__);<@ endif @>
<@ if '__repr__' in specialmethods @>    type->tp_repr = reinterpret_cast<reprfunc>(&obj_<% name %>___repr__);<@ endif @>
<@ if number @>    type->tp_as_number = &obj_<% name %>_number_methods;<@ endif @>
<@ if sequence @>    type->tp_as_sequence = &obj_<% name %>_sequence_methods;<@ endif @>
<@ if mapping @>    type->tp_as_mapping = &obj_<% name %>_mapping_methods;<@ endif @>
<@ if '__hash__' in specialmethods @>    type->tp_hash = reinterpret_cast<hashfunc>(&obj_<% name %>___hash__);<@ endif @>
<@ if '__call__' in specialmethods @>    type->tp_call = reinterpret_cast<ternaryfunc>(&obj_<% name %>___call__);<@ endif @>
<@ if '__str__' in specialmethods @>    type->tp_str = reinterpret_cast<reprfunc>(&obj_<% name %>___str__);<@ endif @>
<@ if '__getattr__' in specialmethods @>    type->tp_getattro = reinterpret_cast<getattrofunc>(&obj_<% name %>___getattr__);<@ endif @>
<@ if '__setattr__' in specialmethods @>    type->tp_setattro = reinterpret_cast<setattrofunc>(&obj_<% name %>___setattr__);<@ endif @>
<@ if doc @>    type->tp_doc = <% doc|quote %>;<@ endif @>
<@ if methodsref @>    type->tp_methods = obj_<% name %>_methods;<@ endif @>
<@ if membersref @>    type->tp_members = obj_<% name %>_members;<@ endif @>
<@ if getsetref @>    type->tp_getset = obj_<% name %>_getset;<@ endif @>
<@ if richcompare @>    type->tp_richcompare = reinterpret_cast<richcmpfunc>(&obj_<% name %>_richcompare);<@ endif @>
<@ if '__iter__' in specialmethods @>    type->tp_iter = reinterpret_cast<getiterfunc>(&obj_<% name %>___iter__);<@ endif @>
<@ if 'next' in specialmethods @>    type->tp_iter = reinterpret_cast<iternextfunc>(&obj_<% name %>_next);<@ endif @>
    type->tp_init = reinterpret_cast<initproc>(&obj_<% name %>_init);
<@ if new_init @>    type->tp_new = &obj_<% name %>_new;<@ endif @>

    return type;
}
== else
PyTypeObject obj_<% name %>Type = {
    PyObject_HEAD_INIT(0)
    0,                         /* ob_size */
    "<% module %>.<% name %>", /* tp_name */
    <% objsize() %>, /* tp_basicsize */
    0,                         /* tp_itemsize */
    <@ if destructref @>reinterpret_cast<destructor>(&obj_<% name %>_dealloc)<@ else @>0<@ endif @>, /* tp_dealloc */
    0,                         /* tp_print */
    0,                         /* tp_getattr */
    0,                         /* tp_setattr */
    <@ if '__cmp__' in specialmethods @>reinterpret_cast<cmpfunc>(&obj_<% name %>___cmp__)<@ else @>0<@ endif @>, /* tp_compare */
    <@ if '__repr__' in specialmethods @>reinterpret_cast<reprfunc>(&obj_<% name %>___repr__)<@ else @>0<@ endif @>, /* tp_repr */
    <@ if number @>&obj_<% name %>_number_methods<@ else @>0<@ endif @>, /* tp_as_number */
    <@ if sequence @>&obj_<% name %>_sequence_methods<@ else @>0<@ endif @>, /* tp_as_sequence */
    <@ if mapping @>&obj_<% name %>_mapping_methods<@ else @>0<@ endif @>, /* tp_as_mapping */
    <@ if '__hash__' in specialmethods @>reinterpret_cast<hashfunc>(&obj_<% name %>___hash__)<@ else @>0<@ endif @>, /* tp_hash */
    <@ if '__call__' in specialmethods @>reinterpret_cast<ternaryfunc>(&obj_<% name %>___call__)<@ else @>0<@ endif @>, /* tp_call */
    <@ if '__str__' in specialmethods @>reinterpret_cast<reprfunc>(&obj_<% name %>___str__)<@ else @>0<@ endif @>, /* tp_str */
    <@ if '__getattr__' in specialmethods @>reinterpret_cast<getattrofunc>(&obj_<% name %>___getattr__)<@ else @>0<@ endif @>, /* tp_getattro */
    <@ if '__setattr__' in specialmethods @>reinterpret_cast<setattrofunc>(&obj_<% name %>___setattr__)<@ else @>0<@ endif @>, /* tp_setattro */
    0,                         /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE|Py_TPFLAGS_CHECKTYPES, /* tp_flags */
    <@ if doc @><% doc|quote %><@ else @>0<@ endif @>, /* tp_doc */
    0,                         /* tp_traverse */
    0,                         /* tp_clear */
    <@ if richcompare @>reinterpret_cast<richcmpfunc>(&obj_<% name %>_richcompare)<@ else @>0<@ endif @>, /* tp_richcompare */
    0,                         /* tp_weaklistoffset */
    <@ if '__iter__' in specialmethods @>reinterpret_cast<getiterfunc>(&obj_<% name %>___iter__)<@ else @>0<@ endif @>, /* tp_iter */
    <@ if 'next' in specialmethods @>reinterpret_cast<iternextfunc>(&obj_<% name %>_next)<@ else @>0<@ endif @>, /* tp_iternext */
    <@ if methodsref @>obj_<% name %>_methods<@ else @>0<@ endif @>, /* tp_methods */
    <@ if membersref @>obj_<% name %>_members<@ else @>0<@ endif @>, /* tp_members */
    <@ if getsetref @>obj_<% name %>_getset<@ else @>0<@ endif @>, /* tp_getset */
    <@ if bases @><% bases|first %><@ else @>0<@ endif @>, /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    reinterpret_cast<initproc>(&obj_<% name %>_init), /* tp_init */
    0,                         /* tp_alloc */
    <@ if new_init @>&obj_<% name %>_new<@ else @>0<@ endif @> /* tp_new */
};
== endif
''')

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
typedef Py_ssize_t type_py_ssize_t;

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
const char *init_on_derived_msg = "__init__ cannot be used directly on a derived type";


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

long narrow(long x,long max,long min) {{
    if(UNLIKELY(x > max || x < min)) {{
        if(min == 0 && x < 0) PyErr_SetString(PyExc_TypeError,"value cannot be negative");
        else PyErr_SetString(PyExc_OverflowError,"value is out of range");
        throw py_error_set();
    }}
    return x;
}}

long PyToXInt(PyObject *po,long max,long min) {{
    return narrow(PyToLong(po),max,min);
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
    #define UIntToPy(x) PyLong_FromUnsignedLong(x)
#else
    #define UIntToPy(x) PyInt_FromLong(x)

    #if INT_MAX == SHRT_MAX
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

PyObject *BoolToPy(bool x) {{
    PyObject *r = x ? Py_True : Py_False;
    Py_INCREF(r);
    return r;
}}

unsigned char py_ssize_t_to_uchar(Py_ssize_t x) {{
    return static_cast<unsigned char>(narrow(x,UCHAR_MAX,0));
}}

signed char py_ssize_t_to_schar(Py_ssize_t x) {{
    return static_cast<signed char>(narrow(x,SCHAR_MAX,SCHAR_MIN));
}}

#if CHAR_MIN == 0
    #define py_ssize_t_to_char(x) py_ssize_t_to_uchar(x)
#else
    #define py_ssize_t_to_char(x) py_ssize_t_to_schar(x)
#endif

unsigned short py_ssize_t_to_ushort(Py_ssize_t x) {{
    return static_cast<unsigned short>(narrow(x,USHRT_MAX,0));
}}

short py_ssize_t_to_sshort(Py_ssize_t x) {{
    return static_cast<short>(narrow(x,SHRT_MAX,SHRT_MIN));
}}

#if (PY_SIZE_MAX>>1) > INT_MAX
    unsigned int py_ssize_t_to_uint(Py_ssize_t x) {{
        return static_cast<unsigned int>(narrow(x,UINT_MAX,0));
    }}

    int py_ssize_t_to_sint(Py_ssize_t x) {{
        return static_cast<int>(narrow(x,INT_MAX,INT_MIN));
    }}
#else
    #define py_ssize_t_to_sint(x) x

    unsigned int py_ssize_t_to_uint(Py_ssize_t x) {{
        return static_cast<unsigned int>(narrow(x,INT_MAX,0));
    }}
#endif

unsigned long py_ssize_t_to_ulong(Py_ssize_t x) {{
    return static_cast<unsigned long>(narrow(x,LONG_MAX,0));
}}


void NoSuchOverload(PyObject *args) {{
    const char *const format = "no overload takes (%s)";
    if(PyTuple_Check(args)) {{
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

            PyErr_Format(PyExc_TypeError,format,msg);
            delete[] msg;
        }} else {{
            PyErr_SetString(PyExc_TypeError,"no overload takes 0 arguments");
        }}
    }} else {{
        PyErr_Format(PyExc_TypeError,format,args->ob_type->tp_name);
    }}
}}



struct obj__Common {{
    PyObject_HEAD
    storage_mode mode;
}};

/* trying to inherit from more than one type raises a TypeError if there isn't a
   common base */
PyTypeObject obj__CommonType = {{
    PyObject_HEAD_INIT(0)
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


module = env.from_string('''
PyMethodDef func_table[] = {
== for f in funclist
    <% f %>,
== endfor
    {0}
};


extern "C" SHARED(void) init<% module %>(void) {
    if(UNLIKELY(PyType_Ready(&obj__CommonType) < 0)) return;

== set classes = classes|list
== for c in classes if not c.dynamic
==     if c.base
    obj_<% c.name %>Type.tp_base = get_obj_<% c.base %>Type();
==     endif
==     if not c.new_init
    obj_<% c.name %>Type.tp_new = &PyType_GenericNew;
==     endif
    if(UNLIKELY(PyType_Ready(&obj_<% c.name %>Type) < 0)) return;
== endfor

    PyObject *m = Py_InitModule3("<% module %>",func_table,<% doc|quote if doc else '0' %>);
    if(UNLIKELY(!m)) return;

    Py_INCREF(&obj__CommonType);
    PyModule_AddObject(m,"_internal_class",reinterpret_cast<PyObject*>(&obj__CommonType));

== for c in classes
==     if c.dynamic
    obj_<% c.name %>Type = create_obj_<% c.name %>Type();
    if(UNLIKELY(!obj_<% c.name %>Type)) return;
    PyModule_AddObject(m,"<% c.name %>",reinterpret_cast<PyObject*>(obj_<% c.name %>Type));
==     else
    Py_INCREF(&obj_<% c.name %>Type);
    PyModule_AddObject(m,"<% c.name %>",reinterpret_cast<PyObject*>(&obj_<% c.name %>Type));
==     endif
== endfor
}

#pragma GCC visibility pop
''')

cast_base = env.from_string('''
<% type %> &cast_base_<% name %>(PyObject *o) {
== if features.managed_ref or not new_init
    switch(reinterpret_cast<obj__Common*>(o)->mode) {
==     if features.managed_ref
    case MANAGEDREF:
        return reinterpret_cast<ref_<% name %>*>(o)->base;
==     endif
    case CONTAINS:
        return reinterpret_cast<obj_<% name %>*>(o)->base;
    default:
        PyErr_SetString(PyExc_RuntimeError,not_init_msg);
        throw py_error_set();
    }
== else
    return reinterpret_cast<obj_<% name %>*>(o)->base;
== endif
}
''')

get_base = '''
{type} &get_base_{name}(PyObject *o) {{
    if(UNLIKELY(!PyObject_TypeCheck(o,get_obj_{name}Type()))) {{
        PyErr_SetString(PyExc_TypeError,"object is not an instance of {name}");
        throw py_error_set();
    }}
    return cast_base_{name}(o);
}}
'''

header_start = env.from_string('''
#pragma once
#ifndef <% module %>_h
#define <% module %>_h

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


#define PY_MEM_NEW_DELETE void *operator new(size_t s) {            \\
        void *ptr = PyMem_Malloc(s);                                \\
        if(!ptr) throw std::bad_alloc();                            \\
        return ptr;                                                 \\
    }                                                               \\
                                                                    \\
    void operator delete(void *ptr) {                               \\
        PyMem_Free(ptr);                                            \\
    }


#pragma GCC visibility push(hidden)

/* when thrown, indicates that a PyErr_X function was already called with the
   details of the exception. As such, it carries no information of its own. */
struct py_error_set {};

enum storage_mode {UNINITIALIZED = 0,CONTAINS,MANAGEDREF};

== if template_assoc
template<typename T> inline PyTypeObject *get_type() {
    int dont_instantiate[sizeof(T) < 0 ? 1 : -1];
    return 0;
}

template<typename T> inline T &cast_base(PyObject *o) {
    int dont_instantiate[sizeof(T) < 0 ? 1 : -1];
}

template<typename T> inline T &get_base(PyObject *o) {
    int dont_instantiate[sizeof(T) < 0 ? 1 : -1];
}
== endif
''')

header_end = '''
#pragma GCC visibility pop

#endif
'''

overload_func_call = env.from_string('''
== if nokwdscheck
        if(kwds && PyDict_Size(kwds)) {
            PyErr_SetString(PyExc_TypeError,no_keywords_msg);
            throw py_error_set();
        }
== endif
<% inner %>

        NoSuchOverload(<% args %>);
        return <% errval %>;
''')

typecheck_start = env.from_string('''
<% type %> &get_base_<% name %>(PyObject *x,bool safe<% ' = true' if not template_assoc %>) {
''')

# The
# reinterpret_cast<long>(static_cast<{type}*>(reinterpret_cast<{othertype}*>(1))) != 1
# part is added as an optimization trick. The purpose of the following code is
# to get a reference to the correct location in memory. Because {othertype}
# derives from more than one type, the memory for {type} wont necessarily be at
# the beginning of {othertype}. If however, {type} does occur at the beginning,
# this added part will evaluate to false and the entire expression should be
# subject to dead code removal by the compiler.
typecheck_test = '''
    if(reinterpret_cast<long>(static_cast<{type}*>(reinterpret_cast<{othertype}*>(1))) != 1 &&
            PyObject_IsInstance(x,reinterpret_cast<PyObject*>(get_obj_{other}Type())))
        return cast_base_{other}(x);
'''

typecheck_else = '''
    if(UNLIKELY(safe && !PyObject_IsInstance(x,reinterpret_cast<PyObject*>(get_obj_{name}Type())))) {{
        PyErr_SetString(PyExc_TypeError,"object is not an instance of {name}");
        throw py_error_set();
    }}
    assert(PyObject_IsInstance(x,reinterpret_cast<PyObject*>(get_obj_{name}Type())));
    return cast_base_{name}(x);
}}
'''

function = '''
{rettype} {name}({args}) {{
    try {{
{code}
    }} EXCEPT_HANDLERS({errval})
{epilog}
}}
'''

number_op = '''
PyObject *obj_{cname}_{op}({args}) {{
    try {{
        if(PyObject_IsInstance(a,reinterpret_cast<PyObject*>(get_obj_{cname}Type()))) {{
{code}
        }} else {{
{rcode}
        }}
    }} EXCEPT_HANDLERS(0)
    Py_INCREF(Py_NotImplemented);
    return Py_NotImplemented;
}}
'''


richcompare_start = '''
PyObject *obj_{name}_richcompare(obj_{name} *self,PyObject *arg,int op) {{
    try {{
{prolog}
        switch(op) {{
'''

richcompare_op = '''
        case {op}:
{code}
            Py_INCREF(Py_NotImplemented);
            return Py_NotImplemented;
'''

richcompare_end = '''
        }
    } EXCEPT_HANDLERS(0)
    return 0;
}
'''

number_methods = env.from_string('''
<@ macro exact(fname) @><@ if fname in specialmethods @>&obj_<% name %>_<% fname %><@ else @>0<@ endif @><@ endmacro @>
<@ macro cast(fname,type) @><@ if fname in specialmethods @>reinterpret_cast<<% type %>>(&obj_<% name %>_<% fname %>)<@ else @>0<@ endif @><@ endmacro @>
PyNumberMethods obj_<% name %>_number_methods = {
    <% exact('__add__') %>,
    <% exact('__sub__') %>,
    <% exact('__mul__') %>,
    <% exact('__div__') %>,
    <% exact('__mod__') %>,
    <% exact('__divmod__') %>,
    <% exact('__pow__') %>,
    <% cast('__neg__','unaryfunc') %>,
    <% cast('__pos__','unaryfunc') %>,
    <% cast('__abs__','unaryfunc') %>,
    <% cast('__nonzero__','inquiry') %>,
    <% cast('__invert__','unaryfunc') %>,
    <% exact('__lshift__') %>,
    <% exact('__rshift__') %>,
    <% exact('__and__') %>,
    <% exact('__xor__') %>,
    <% exact('__or__') %>,
    <% exact('__coerce__') %>,
    <% cast('__int__','unaryfunc') %>,
    <% cast('__long__','unaryfunc') %>,
    <% cast('__float__','unaryfunc') %>,
    <% cast('__oct__','unaryfunc') %>,
    <% cast('__hex__','unaryfunc') %>,
    <% cast('__iadd__','binaryfunc') %>,
    <% cast('__isub__','binaryfunc') %>,
    <% cast('__imul__','binaryfunc') %>,
    <% cast('__idiv__','binaryfunc') %>,
    <% cast('__imod__','binaryfunc') %>,
    <% cast('__ipow__','ternaryfunc') %>,
    <% cast('__ilshift__','binaryfunc') %>,
    <% cast('__irshift__','binaryfunc') %>,
    <% cast('__iand__','binaryfunc') %>,
    <% cast('__ixor__','binaryfunc') %>,
    <% cast('__ior__','binaryfunc') %>,
    <% exact('__floordiv__') %>,
    <% exact('__truediv__') %>,
    <% cast('__ifloordiv__','binaryfunc') %>,
    <% cast('__itruediv__','binaryfunc') %>,
    <% cast('__index__','unaryfunc') %>,
};
''')

ret_notimplemented = '''
    Py_INCREF(Py_NotImplemented);
    return Py_NotImplemented;
'''

mapping_methods = env.from_string('''
PyMappingMethods obj_<% name %>_mapping_methods = {
    <@ if '__mapping_length__' in specialmethods @>reinterpret_cast<lenfunc>(&obj_<% name %>___mapping_length__)<@ else @>0<@ endif @>,
    <@ if '__mapping__getitem__' in specialmethods @>reinterpret_cast<binaryfunc>(&obj_<% name %>___mapping__getitem__)<@ else @>0<@ endif @>,
    <@ if '__mapping__setitem__' in specialmethods @>reinterpret_cast<objobjargproc>(&obj_<% name %>___mapping__setitem__)<@ else @>0<@ endif @>
};
''')

sequence_methods = env.from_string('''
PySequenceMethods obj_<% name %>_sequence_methods = {
    <@ if '__sequence_length__' in specialmethods @>reinterpret_cast<lenfunc>(&obj_<% name %>___sequence_length__)<@ else @>0<@ endif @>,
    <@ if '__concat__' in specialmethods @>reinterpret_cast<binaryfunc>(&obj_<% name %>___concat__)<@ else @>0<@ endif @>,
    <@ if '__repeat__' in specialmethods @>reinterpret_cast<ssizeargfunc>(&obj_<% name %>___repeat__)<@ else @>0<@ endif @>,
    <@ if '__sequence__getitem__' in specialmethods @>reinterpret_cast<ssizeargfunc>(&obj_<% name %>___sequence__getitem__)<@ else @>0<@ endif @>,
    0,
    <@ if '__sequence__setitem__' in specialmethods @>reinterpret_cast<ssizeobjargproc>(&obj_<% name %>___sequence__setitem__)<@ else @>0<@ endif @>,
    0,
    <@ if '__contains__' in specialmethods @>reinterpret_cast<lenfunc>(&obj_<% name %>___contains__)<@ else @>0<@ endif @>,
    <@ if '__iconcat__' in specialmethods @>reinterpret_cast<lenfunc>(&obj_<% name %>___iconcat__)<@ else @>0<@ endif @>,
    <@ if '__irepeat__' in specialmethods @>reinterpret_cast<lenfunc>(&obj_<% name %>___irepeat__)<@ else @>0<@ endif @>
};
''')

subclass = env.from_string('''
class <% name %>_virt_handler : public <% type %> {
public:
== for con in constructors
    <% name %>_virt_handler(<% con.args %>) : <% type %>(<% con.argvals %>) {}
== endfor

== for m in methods
    <% m.ret %> <% m.name %>(<% m.args %>)<% ' const' if m.const %>;
== endfor

protected:
    PyObject *self() const;
};
''')

subclass_meth = env.from_string('''
inline PyObject *<% name %>_virt_handler::self() const {
    return reinterpret_cast<PyObject*>(reinterpret_cast<size_t>(this) - offsetof(obj_<% name %>,base));
}
''')

virtmethod = env.from_string('''
<% ret %> <% cname %>_virt_handler::<% func %>(<% args %>)<% ' const' if const %> {
    PyObject *f = PyObject_GetAttrString(self(),"<% name %>");
    if(!f) throw py_error_set(); // TODO: throw better exception
    if(PyCFunction_Check(f) && PyCFunction_GET_FUNCTION(f) == reinterpret_cast<PyCFunction>(&obj_<% cname %>_method_<% name %>)) {
        <% 'return ' if ret != 'void' %><% type %>::<% func %>(<% argvals %>);
    } else {
        PyObject *ret = PyObject_CallFunctionObjArgs(f,<% pyargvals %>NULL);
        if(!ret) throw py_error_set(); // TODO: throw better exception
        <@ if ret != 'void' @>return <% retfrompy %>;<@ endif @>
    }
}
''')
