# the template strings used by espec.py, put here to keep espec.py uncluttered

import jinja2



class Tab:
    """Yield 4 x self.amount whitespace characters when converted to a string.

    An instance can be added to or subtracted from directly, to add to or
    subtract from "amount".

    """
    def __init__(self,amount = 1):
        if isinstance(amount,Tab):
            self.amount = amount.amount # copy constructor
        else:
            self.amount = amount

    def __str__(self):
        return self.amount * 4 * ' '

    def __repr__(self):
        return 'Tab({0})'.format(self.amount)

    # in-place addition/subtraction omitted to prevent modification when passed
    # as an argument to a function

    def __add__(self,val):
        if isinstance(val,basestring):
            return self.__str__() + val
        return Tab(self.amount + val)

    def __radd__(self,val):
        if isinstance(val,basestring):
            return val + self.__str__()
        return Tab(self.amount + val)

    def __sub__(self,val):
        return Tab(self.amount - val)

    def line(self,x):
        return self.__str__() + x + '\n'


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
== if features or destructor or ((instance_dict or weakref) and not new_init)
    switch(self->mode) {
==     if destructor or instance_dict or weakref
    case CONTAINS:
        self->~obj_<% name %>();
        break;
==     endif
==     if MANAGED_REF in features
    case MANAGEDREF:
        reinterpret_cast<ref_<% name %>*>(self)->~ref_<% name %>();
        break;
==     endif
==     if MANAGED_PTR in features
    case MANAGEDPTR:
        reinterpret_cast<ptr_<% name %>*>(self)->~ptr_<% name %>();
        break;
==     endif
==     if UNMANAGED_REF in features and (instance_dict or weakref)
    case UNMANAGEDREF:
        reinterpret_cast<uref_<% name %>*>(self)->~uref_<% name %>();
        break;
==     endif
=#     TODO: this default case is not always needed
    default:
==     if instance_dict
        Py_XDECREF(self->idict);
==     endif
==     if weakref
        if(self->weaklist) PyObject_ClearWeakRefs(reinterpret_cast<PyObject*>(self));
==     endif
        break;
    }
== elif instance_dict or weakref
    self->~obj_<% name %>();
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

# The weaklist and instance dict variables are always placed below 'base' so
# that the offset of 'base' is always the same as in inherited classes.
# Although if all derived and inherited classes have the same combination of
# these variables, they can be placed before 'base', which may be more space
# efficient because the padding below would not be required.
classdef = env.from_string('''
extern PyTypeObject <% '*' if dynamic %>obj_<% name %>Type;
inline PyTypeObject *get_obj_<% name %>Type() { return <% '&' if not dynamic %>obj_<% name %>Type; }

== set common_base = (weakref or instance_dict) and features
== if common_base
/* we need multiple classes with the extra dictionaries at the exact same
   offset, so we'll derive all the classes from the same type */
struct _x_<% name %> {
    PyObject_HEAD
    storage_mode mode;
    union {
==     if MANAGED_REF in features
        struct {
            <% type %> *base;
            PyObject *container;
        } ref;
==     endif
==     if MANAGED_PTR in features or UNMANAGED_REF in features
        <% type %> *ptr;
==     endif
==     if not uninstantiatable
        char base[sizeof(<% type %>)];
==     endif
        double x; // to force alignment
    };
==     if instance_dict
    PyObject *idict;
==     endif
==     if weakref
    PyObject *weaklist;
==     endif

protected:
    _x_<% name %>() {
==     if instance_dict
        idict = 0;
==     endif
==     if weakref
        weaklist = 0;
==     endif
    }
    ~_x_<% name %>() {
==     if instance_dict
        Py_XDECREF(idict);
==     endif
==     if weakref
        if(weaklist) PyObject_ClearWeakRefs(reinterpret_cast<PyObject*>(this));
==     endif
    }
};
== endif

== if MANAGED_REF in features
struct ref_<% name %><@ if common_base @> : _x_<% name %><@ endif @> {
==     if not common_base
    PyObject_HEAD
    storage_mode mode;
    struct {
        <% type %> *base;
        PyObject *container;
    } ref;
==     endif

    PY_MEM_<@ if gc @>GC_<@ endif @>NEW_DELETE

    ref_<% name %>(<% type %> &base,PyObject *container) {
        mode = MANAGEDREF;
        ref.base = &base;
        ref.container = container;
        Py_INCREF(container);
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_<% name %>Type());
    }

    ~ref_<% name %>() {
        Py_DECREF(ref.container);
    }
};

== endif
== if MANAGED_PTR in features
struct ptr_<% name %><@ if common_base @> : _x_<% name %><@ endif @> {
==     if not common_base
    PyObject_HEAD
    storage_mode mode;
    <% type %> *ptr;
==     endif

    PY_MEM_<@ if gc @>GC_<@ endif @>NEW_DELETE

    ptr_<% name %>(<% type %> *base) {
        mode = MANAGEDPTR;
        ptr = base;
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_<% name %>Type());
    }

    ~ptr_<% name %>() {
        delete ptr;
    }
};

== endif
== if UNMANAGED_REF in features
struct uref_<% name %><@ if common_base @> : _x_<% name %><@ endif @> {
==     if not common_base
    PyObject_HEAD
    storage_mode mode;
    <% type %> *ptr;
==     endif

    PY_MEM_<@ if gc @>GC_<@ endif @>NEW_DELETE

    uref_<% name %>(<% type %> &base) {
        mode = UNMANAGEDREF;
        ptr = &base;
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_<% name %>Type());
    }
};

== endif

struct obj_<% name %><@ if common_base @> : _x_<% name %><@ endif @> {
== if not common_base
    PyObject_HEAD
==     if mode_var
    storage_mode mode;
==     endif
==     if uninstantiatable
    /* a dummy type whose offset in the struct should be the same as any derived
       type's */
    union {
        double a;
        void *b;
    } base;
==     else
    <% type %> base;
==         if instance_dict
    PyObject *idict;
==         endif
==         if weakref
    PyObject *weaklist;
==         endif
==     endif
== endif

== if not uninstantiatable
    PY_MEM_<@ if gc @>GC_<@ endif @>NEW_DELETE

==     for con in constructors
    obj_<% name %>(<% con.args %>) <@ if not common_base @>: base(<% con.argvals %>)<@ if instance_dict @>, idict(0)<@ endif @><@ if weakref @>, weaklist(0)<@ endif @> <@ endif @>{
==         if common_base
        new(&base) <% type %>(<% con.argvals %>);
==         endif
        PyObject_Init(reinterpret_cast<PyObject*>(this),get_obj_<% name %>Type());
==         if mode_var
        mode = CONTAINS;
==         endif
    }
==     endfor
==     if (common_base and destructor) or ((instance_dict or weakref) and not common_base)
    ~obj_<% name %>() {
==         if common_base
==             if destructor
        reinterpret_cast<<% type %>&>(base).<% destructor %>();
==             endif
==         else
==             if instance_dict
        Py_XDECREF(idict);
==             endif
==             if weakref
        if(weaklist) PyObject_ClearWeakRefs(reinterpret_cast<PyObject*>(this));
==             endif
==         endif
    }
==     endif
== endif
};

#ifdef PYEXPOSE_TEMPLATE_HELPERS
template<> inline PyTypeObject *get_type<<% original_type %> >() {
    return get_obj_<% name %>Type();
}

<% original_type %> &get_base_<% name %>(PyObject *o<% ',bool safe=true' if bool_arg_get %>);
template<> inline <% original_type %> &get_base<<% original_type %> >(PyObject *o) {
    return get_base_<% name %>(o);
}

template<> struct wrapped_type<<% original_type %> > {
    typedef obj_<% name %> type;
};

== if invariable
template<> struct invariable_storage<<% original_type %> > {
    enum {value = 1};
};
== endif
#endif
''')

classtypedef = env.from_string('''
== if initcode
int obj_<% name %>_init(obj_<% name %> *self,PyObject *args,PyObject *kwds) {
==     if derived
    if(UNLIKELY(
==         for d in derived
        <@ if not loop.first @>|| <@ endif @>PyObject_TypeCheck(reinterpret_cast<PyObject*>(self),get_obj_<% d %>Type())
==         endfor
    )) {
        PyErr_SetString(PyExc_TypeError,init_on_derived_msg);
        return -1;
    }
==     endif

    <% type %> *addr = reinterpret_cast<<% type %>*>(&self->base);

=#     before we can call the constructor, the destructor needs to be called if
=#     we already have an initialized object
==     if features or (destructor and not newinitcode)
    switch(self->mode) {
=#         The ref_X, ptr_X and uref_X all store an address to the contained
=#         type, in the same place. We'll need to pick one that exists.
=#         Even with MANAGEDPTR, we can't delete the pointer and switch to
=#         CONTAINS because there may be another pointer with the same address.
==         if features
==             if MANAGED_REF in features
==                 set addr = 'reinterpret_cast<ref_' ~ name ~ '*>(self)->ref.base'
    case MANAGEDREF:
==             endif
==             if MANAGED_PTR in features
==                 set addr = 'reinterpret_cast<ptr_' ~ name ~ '*>(self)->ptr'
    case MANAGEDPTR:
==             endif
==             if UNMANAGED_REF in features
==                 set addr = 'reinterpret_cast<uref_' ~ name ~ '*>(self)->ptr'
    case UNMANAGEDREF:
==             endif
        addr = <% addr %>;
==             if destructor
        addr-><% destructor %>();
==             endif
        break;
==         endif
==         if destructor
    case CONTAINS:
        self->base.<% destructor %>();
        break;
==         endif
    default:
        assert(self->mode == UNINITIALIZED);
        self->mode = CONTAINS;
        break;
    }
==     elif destructor
    self->base.<% destructor %>();
==     elif not newinitcode
    self->mode = CONTAINS;
==     endif
    try {
<% initcode %>
    } EXCEPT_HANDLERS(-1)
success:
    return 0;
}
== endif

== if newinitcode or not initcode
PyObject *obj_<% name %>_new(PyTypeObject *type,PyObject *args,PyObject *kwds) {
== if newinitcode
    obj_<% name %> *ptr = reinterpret_cast<obj_<% name %>*>(type->tp_alloc(type,0));
    if(ptr) {
        try {
            try {
<% newinitcode %>
            } catch(...) {
                Py_DECREF(ptr);
            }
        } EXCEPT_HANDLERS(0)
success:
==     if features or destructor or weakref
==         if features or destructor
        ptr->mode = CONTAINS;
==         endif
==         if weakref
        ptr->weaklist = 0;
==         endif
==     else
        ;
==     endif
    }
    return reinterpret_cast<PyObject*>(ptr);
== else
    PyErr_SetString(PyExc_TypeError,"The <% name %> type cannot be instantiated");
    return 0;
== endif
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

    type->tp_basicsize = sizeof(obj_<% name %>);
    type->tp_flags |= Py_TPFLAGS_CHECKTYPES<@ if gc @>|Py_TPFLAGS_HAVE_GC<@ endif @>;
    type->tp_dictoffset = <@if instance_dict @>offsetof(obj_<% name %>,idict)<@ else @>0<@ endif @>;
    type->tp_weaklistoffset = <@if weakref @>offsetof(obj_<% name %>,weaklist)<@ else @>0<@ endif @>;
<@ if dealloc @>    type->tp_dealloc = reinterpret_cast<destructor>(&obj_<% name %>_dealloc);<@ endif @>
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
==     if methodsref
    type->tp_methods = obj_<% name %>_methods;
==     endif
==     if membersref
    type->tp_members = obj_<% name %>_members;
==     endif
==     if getsetref
    type->tp_getset = obj_<% name %>_getset;
==     endif
==     if richcompare
    type->tp_richcompare = reinterpret_cast<richcmpfunc>(&obj_<% name %>_richcompare);
==     endif
==     if '__iter__' in specialmethods
    type->tp_iter = reinterpret_cast<getiterfunc>(&obj_<% name %>___iter__);
==     endif
==     if 'next' in specialmethods
    type->tp_iter = reinterpret_cast<iternextfunc>(&obj_<% name %>_next);
==     endif
==     if initcode
    type->tp_init = reinterpret_cast<initproc>(&obj_<% name %>_init);
==     endif
==     if newinitcode or not initcode
    type->tp_new = &obj_<% name %>_new;
==     endif
==     if gc
    type->tp_traverse = reinterpret_cast<traverseproc>(&obj_<% name %>_traverse);
==     endif
==     if gc_clear
    type->tp_clear = reinterpret_cast<inquiry>(&obj_<% name %>_clear);
==     endif

    return type;
}
== else
PyTypeObject obj_<% name %>Type = {
    PyVarObject_HEAD_INIT(0,0)
    "<% module %>.<% name %>", /* tp_name */
    sizeof(obj_<% name %>), /* tp_basicsize */
    0,                         /* tp_itemsize */
    <@ if dealloc @>reinterpret_cast<destructor>(&obj_<% name %>_dealloc)<@ else @>0<@ endif @>, /* tp_dealloc */
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
    Py_TPFLAGS_DEFAULT|Py_TPFLAGS_BASETYPE|Py_TPFLAGS_CHECKTYPES<@ if gc @>|Py_TPFLAGS_HAVE_GC<@ endif @>, /* tp_flags */
    <@ if doc @><% doc|quote %><@ else @>0<@ endif @>, /* tp_doc */
    <@ if gc @>reinterpret_cast<traverseproc>(&obj_<% name %>_traverse)<@ else @>0<@ endif @>, /* tp_traverse */
    <@ if gc_clear @>reinterpret_cast<inquiry>(&obj_<% name %>_clear)<@ else @>0<@ endif @>, /* tp_clear */
    <@ if richcompare @>reinterpret_cast<richcmpfunc>(&obj_<% name %>_richcompare)<@ else @>0<@ endif @>, /* tp_richcompare */
    <@if weakref @>offsetof(obj_<% name %>,weaklist)<@ else @>0<@ endif @>, /* tp_weaklistoffset */
    <@ if '__iter__' in specialmethods @>reinterpret_cast<getiterfunc>(&obj_<% name %>___iter__)<@ else @>0<@ endif @>, /* tp_iter */
    <@ if 'next' in specialmethods @>reinterpret_cast<iternextfunc>(&obj_<% name %>_next)<@ else @>0<@ endif @>, /* tp_iternext */
    <@ if methodsref @>obj_<% name %>_methods<@ else @>0<@ endif @>, /* tp_methods */
    <@ if membersref @>obj_<% name %>_members<@ else @>0<@ endif @>, /* tp_members */
    <@ if getsetref @>obj_<% name %>_getset<@ else @>0<@ endif @>, /* tp_getset */
    <@ if bases @><% bases|first %><@ else @>0<@ endif @>, /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    <@if instance_dict @>offsetof(obj_<% name %>,idict)<@ else @>0<@ endif @>, /* tp_dictoffset */
    <@ if initcode @>reinterpret_cast<initproc>(&obj_<% name %>_init)<@ else @>0<@ endif @>, /* tp_init */
    0,                         /* tp_alloc */
    <@ if newinitcode or not initcode @>&obj_<% name %>_new<@ else @>0<@ endif @> /* tp_new */
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
typedef PyObject *type_pyobject;
typedef visitproc type_visitproc;

'''

# the back-slashes will line up after the double curly braces are replaced with single curly braces
module_start = '''
#include <Python.h>
#include <structmember.h>
#include <exception>
#include <assert.h>
{includes}
#include "{module}.h"


#ifndef PyVarObject_HEAD_INIT
    #define PyVarObject_HEAD_INIT(type, size) \\
        PyObject_HEAD_INIT(type) size,
#endif


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
const char *not_implemented_msg = "This method is not implemented";


struct get_arg {{
    PyObject *args, *kwds;
    unsigned int tcount, kcount;
    get_arg(PyObject *args,PyObject *kwds) : args(args), kwds(kwds), tcount(0), kcount(0) {{
        assert(args != 0 && PyTuple_Check(args));
        assert(kwds == 0 || PyDict_Check(kwds));
    }}

    PyObject *operator()(const char *name,bool required);
    void finished(const char *names[]);
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

void get_arg::finished(const char *names[]) {{
    // TODO: check for unused arguments
}}



long narrow(long x,long max,long min) {{
    if(UNLIKELY(x > max || x < min)) {{
        if(min == 0 && x < 0) PyErr_SetString(PyExc_TypeError,"value cannot be negative");
        else PyErr_SetString(PyExc_OverflowError,"value is out of range");
        throw py_error_set();
    }}
    return x;
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

'''

obj_internal = env.from_string('''
struct _obj_Internal<% suffix %> {
    PyObject_HEAD
    storage_mode mode;
== if instance_dict
    PyObject *idict;
== endif
== if weakref
    PyObject *weaklist;
== endif
};

PyTypeObject _obj_Internal<% suffix %>Type = {
    PyVarObject_HEAD_INIT(0,0)
    "<% module %>._internal_class<% suffix %>", /* tp_name */
    sizeof(_obj_Internal<% suffix %>), /* tp_basicsize */
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
    <@ if weakref @>offsetof(_obj_Internal<% suffix %>,weaklist)<@ else @>0<@ endif @>, /* tp_weaklistoffset */
    0,                         /* tp_iter */
    0,                         /* tp_iternext */
    0,                         /* tp_methods */
    0,                         /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    <@ if instance_dict @>offsetof(_obj_Internal<% suffix %>,idict)<@ else @>0<@ endif @>, /* tp_dictoffset */
    0,                         /* tp_init */
    0,                         /* tp_alloc */
    0                          /* tp_new */
};
''')


module = env.from_string('''
PyMethodDef func_table[] = {
== for f in funclist
    <% f %>,
== endfor
    {0}
};

#if PY_MAJOR_VERSION >= 3
#define INIT_ERR_VAL 0

struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    "<% module %>",
    <% doc|quote if doc else '0' %>,
    -1,
    func_table,
    0,
    0,
    0,
    0
};

extern "C" SHARED(PyObject*) PyInit_<% module %>(void) {
#else
#define INIT_ERR_VAL

extern "C" SHARED(void) init<% module %>(void) {
#endif
== if wrap_in_trycatch
    try {
== endif
<% init_pre %>

== for suf in internal_suffixes
    if(UNLIKELY(PyType_Ready(&_obj_Internal<% suf %>Type) < 0)) return INIT_ERR_VAL;
== endfor

== for c in classes if not c.dynamic
==     if c.base
    obj_<% c.name %>Type.tp_base = get_obj_<% c.base %>Type();
==     endif
==     if not (c.new_init or c.no_init)
    obj_<% c.name %>Type.tp_new = &PyType_GenericNew;
==     endif
    if(UNLIKELY(PyType_Ready(&obj_<% c.name %>Type) < 0)) return INIT_ERR_VAL;
== endfor

#if PY_MAJOR_VERSION >= 3
    PyObject *m = PyModule_Create(&module_def);
#else
    PyObject *m = Py_InitModule3("<% module %>",func_table,<% doc|quote if doc else '0' %>);
#endif
    if(UNLIKELY(!m)) return INIT_ERR_VAL;

== for suf in internal_suffixes
    Py_INCREF(&_obj_Internal<% suf %>Type);
    PyModule_AddObject(m,"_internal_class<% suf %>",reinterpret_cast<PyObject*>(&_obj_Internal<% suf %>Type));
== endfor

== for c in classes
==     if c.dynamic
    obj_<% c.name %>Type = create_obj_<% c.name %>Type();
    if(UNLIKELY(!obj_<% c.name %>Type)) return INIT_ERR_VAL;
    PyModule_AddObject(m,"<% c.name %>",reinterpret_cast<PyObject*>(obj_<% c.name %>Type));
==     else
    Py_INCREF(&obj_<% c.name %>Type);
    PyModule_AddObject(m,"<% c.name %>",reinterpret_cast<PyObject*>(&obj_<% c.name %>Type));
==     endif
== endfor

== for v in vars
==     if loop.first and not wrap_in_trycatch
    try {
==     endif
        PyModule_AddObject(m,"<% v.name %>",<% v.create %>);
==     if loop.last and not wrap_in_trycatch
    } catch(std::bad_alloc&) {
        PyErr_NoMemory();
        return INIT_ERR_VAL;
    }
==     endif
== endfor

<% init_post %>
== if wrap_in_trycatch
    } EXCEPT_HANDLERS(INIT_ERR_VAL)
== endif

#if PY_MAJOR_VERSION >= 3
    return m;
#endif
}

#pragma GCC visibility pop
''')

cast_base = env.from_string('''
<% type %> &cast_base_<% name %>(PyObject *o) {
== if features or not new_init
    switch(reinterpret_cast<obj_<% name %>*>(o)->mode) {
    case CONTAINS:
        return reinterpret_cast<<% type %>&>(reinterpret_cast<obj_<% name %>*>(o)->base);
=#     The ref_X, ptr_X and uref_X all store an address to the contained type,
=#     in the same place. We'll need to pick one that exists.
==     if features
==         if MANAGED_REF in features
==             set addr = 'reinterpret_cast<ref_' ~ name ~ '*>(o)->ref.base'
    case MANAGEDREF:
==         endif
==         if MANAGED_PTR in features
==             set addr = 'reinterpret_cast<ptr_' ~ name ~ '*>(o)->ptr'
    case MANAGEDPTR:
==         endif
==         if UNMANAGED_REF in features
==             set addr = 'reinterpret_cast<uref_' ~ name ~ '*>(o)->ptr'
    case UNMANAGEDREF:
==         endif
        return *<% addr %>;
==     endif
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

#include "pyexpose_common.h"


#pragma GCC visibility push(hidden)

''')

header_end = '''
#pragma GCC visibility pop

#endif
'''

no_keywords_check = '''
        if(kwds && PyDict_Size(kwds)) {
            PyErr_SetString(PyExc_TypeError,no_keywords_msg);
            throw py_error_set();
        }
'''

no_such_overload = '''
        NoSuchOverload({args});
        return {errval};
'''

typecheck_start = env.from_string('''
#ifdef PYEXPOSE_TEMPLATE_HELPERS
<% type %> &get_base_<% name %>(PyObject *x,bool safe) {
#else
<% type %> &get_base_<% name %>(PyObject *x,bool safe=true) {
#endif
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
            PyObject_TypeCheck(x,get_obj_{other}Type()))
        return cast_base_{other}(x);
'''

typecheck_else = '''
    if(UNLIKELY(safe && !PyObject_TypeCheck(x,get_obj_{name}Type()))) {{
        PyErr_SetString(PyExc_TypeError,"object is not an instance of {name}");
        throw py_error_set();
    }}
    assert(PyObject_TypeCheck(x,get_obj_{name}Type()));
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
        if(PyObject_TypeCheck(a,get_obj_{cname}Type())) {{
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
    if(!f) throw py_error_set();
    if(PyCFunction_Check(f) && PyCFunction_GET_FUNCTION(f) == reinterpret_cast<PyCFunction>(&obj_<% cname %>_method_<% name %>)) {
== if pure
        PyErr_SetString(PyExc_NotImplementedError,not_implemented_msg);
== else
        <% 'return ' if ret != 'void' %><% type %>::<% func %>(<% argvals %>);
== endif
    } else {
        PyObject *ret = PyObject_CallFunctionObjArgs(f,<% pyargvals %>NULL);
        if(!ret) throw py_error_set();
        <@ if ret != 'void' @><% rettype %> cret = <% retfrompy %>;<@ endif @>
        Py_DECREF(ret);
        <@ if ret != 'void' @>return cret;<@ endif @>
    }
}
''')

new_uref = 'reinterpret_cast<PyObject*>(new uref_{0}({1}))'

traverse_shell = '''
int obj_{0}_traverse(obj_{0} *self,visitproc visit,void *arg) {{
{1}
    return 0;
}}
'''

clear_shell = '''
int obj_{0}_clear(obj_{0} *self) {{
{1}
    return 0;
}}
'''

traverse_pyobject = '''
    if({0}) {{
        int ret = visit({0},arg);
        if(ret) return ret;
    }}
'''

traverse_t_func = '''
    {{{{
        int ret = ({{0}}).{0}(visit,arg);
        if(ret) return ret;
    }}}}
'''

clear_pyobject = '''
    if({0}) {{
        PyObject *tmp = {0};
        {0} = 0;
        Py_DECREF(tmp);
    }}
'''

field_offset_and_type = '''
const unsigned long class_{0}_field_offset_{1} = __builtin_offsetof(class_type_{0},{2});
typedef __typeof__(reinterpret_cast<class_type_{0}*>(1)->{2}) class_{0}_field_type_{1};
'''
