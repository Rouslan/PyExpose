
import re
import itertools
import os.path
import copy
import sys
import textwrap

from xmlparse import *
import err
import gccxml
import espectmpl as tmpl


TEST_NS = "___gccxml_types_test_ns___"
UNINITIALIZED_ERR_TYPE = "PyExc_RuntimeError"

RET_COPY = 1
RET_MANAGED_REF = 2

GETTER = 1
SETTER = 2


SF_NO_ARGS = 1 # (PyObject *self)
SF_ONE_ARG = 2 # (PyObject *self, PyObject *o)
SF_TWO_ARGS = 3  # (PyObject *self, PyObject *o1, PyObject *o2)
SF_KEYWORD_ARGS = 4 # (PyObject *self, PyObject *args, PyObject *kwds)
SF_COERCE_ARGS = 5 # (PyObject **p1, PyObject **p2)
SF_SSIZE_ARG = 6 # (PyObject *self, Py_ssize_t i)
SF_SSIZE_OBJ_ARGS = 7 # (PyObject *self, Py_ssize_t i, PyObject *o)
SF_TYPE_KEYWORD_ARGS = 8 # (PyTypeObject *subtype, PyObject *args, PyObject *kwds)

SF_RET_OBJ = 0
SF_RET_INT = 1
SF_RET_LONG = 2
SF_RET_SSIZE = 3


TYPE_FLOAT = 1
TYPE_INT = 2
TYPE_LONG = 3
TYPE_STR = 4
TYPE_UNICODE = 5
TYPES_LIST = range(1,6)


# A table specifying what checks to make when matching a Python number to a specific C++ overload

CHECK_FLOAT = 0b100
CHECK_INT = 0b010
CHECK_LONG = 0b001

coercion = [
    (None,             None,             None            ),
    (None,             None,             'PyNumber_Check'),
    (None,             'PyNumber_Check', None            ),
    (None,             'PyInt_Check',    'PyNumber_Check'),
    ('PyNumber_Check', None,             None            ),
    ('PyFloat_Check',  None,             'PyNumber_Check'),
    ('PyFloat_Check',  'PyNumber_Check', None            ),
    ('PyFloat_Check',  'PyInt_Check',    'PyLong_Check'  )
]


class SpecificationError(err.Error):
    def __str__(self):
        return 'Specification Error: ' + super(SpecificationError,self).__str__()




def getDestructor(self):
    for m in self.members:
        if isinstance(m,gccxml.CPPDestructor):
            return m
    return None
gccxml.CPPClass.getDestructor = getDestructor

def same_args(a,b):
    return len(a) == len(b) and all(str(ai) == str(bi) for ai,bi in zip(a,b))

def getConstructor(self,ovrld = None):
    constructors = [c for c in self.members if isinstance(c,gccxml.CPPConstructor) and c.access == gccxml.ACCESS_PUBLIC]
    if ovrld:
        for c in constructors:
            if same_args(ovrld,c.args): return c
        msg = 'For class "{0}", there is no public constructor with the arguments specified by "overload"'.format(self.name)
        if constructors:
            msg += '\nThe available constructors are:' + ''.join('\n({0})'.format(','.join(map(str,c.args))) for c in constructors)
        raise SpecificationError(msg)
    else:
        realconstructs = [c for c in constructors if not c.artificial]
        if len(realconstructs) == 1:
            return realconstructs[0]
        else:
            # if there is more than one constructor and an overload wasn't specified, look for one with no arguments (the default constructor)
            for c in constructors:
                if not c.args: return c
            raise SpecificationError('There is more than one constructor and there is no default. An overload must be specified in the spec file.')
gccxml.CPPClass.getConstructor = getConstructor

class LevelBaseTraverser:
    """An iterator where the first item is a list containing the supplied class
    (CPPClass) and each subsequent item is a list of the immediate base classes
    of the classes in the previous list."""

    def __init__(self,c,access = gccxml.ACCESS_PUBLIC):
        self.nodes =  [c]
        self.access = access

    def __iter__(self):
        return self

    def next(self):
        if not self.nodes:
            raise StopIteration()

        temp = self.nodes
        self.nodes = [b.type for b in itertools.chain.from_iterable(n.bases for n in temp) if self.access >= b.access]
        return temp

def real_type(x):
    return real_type(x.type) if isinstance(x,gccxml.CPPTypeDef) else x

def _namespace_find(self,x):
    parts = x.split('::',1)
    levels = LevelBaseTraverser(self) if isinstance(self,gccxml.CPPClass) else [[self]]
    for l in levels:
        matches = [real_type(m) for m in itertools.chain.from_iterable(i.members for i in l) if hasattr(m,"canon_name") and m.canon_name == parts[0]]

        if matches:
            if len(parts) == 2:
                if not isinstance(matches[0],(gccxml.CPPClass,gccxml.CPPNamespace)):
                    raise SpecificationError('"{0}" is not a namespace, struct or class'.format(parts[0]))

                assert len(matches) == 1
                return _namespace_find(matches[0],parts[1])

            return matches
    return []

def namespace_find(self,x):
    if x.startswith('::'): # explicit global namespace
        while self.context: self = self.context
        r = _namespace_find(self,x[2:])
        if r: return r
    else:
        # if the symbol isn't found in this scope, check the outer scope
        while self:
            r = _namespace_find(self,x)
            if r: return r
            self = self.context

    raise SpecificationError('could not find "{0}"'.format(x))

gccxml.CPPClass.find = namespace_find
gccxml.CPPNamespace.find = namespace_find



def mandatory_args(x):
    return len(list(itertools.takewhile(lambda a: a.default is None, x.args)))

def can_accept(x,args):
    if not (mandatory_args(x) <= args <= len(x.args)):
        raise SpecificationError('"{0}" must take {1} argument(s)'.format(x.canon_name,args))

def varargs(x):
    return x.args and x.args[-1] is gccxml.cppellipsis


class ObjFeatures:
    """Specifies the way a class may be stored.

    managed_ref -- A reference and the object that actually holds the object is
        stored
    managed_ptr -- A pointer is held and delete will be called upon destruction
    unmanaged_ref -- A reference is stored. The object's lifetime is not managed

    """
    managed_ref = False
    managed_ptr = False
    unmanaged_ref = False


class MultiInheritNode:
    def __init__(self,first):
        self.classes = [first]
        self.derived_nodes = []

    def new_node(self,first):
        n = MultiInheritNode(first)
        self.derived_nodes.append(n)
        return n

    def append_single(self,item):
        self.classes.append(item)

    @property
    def main_type(self):
        assert self.classes
        return self.classes[0]

    def output(self,root):
        r = ''
        for d in self.derived_nodes:
            r += d.output(root)
        r += tmpl.typecheck_test.format(
            type = root.main_type.type.canon_name,
            othertype = self.main_type.type.canon_name,
            other = self.main_type.name)
        return r

    def downcast_func(self,features):
        r = tmpl.typecheck_start.format(
            name = self.main_type.name,
            type = self.main_type.type.canon_name)

        for d in self.derived_nodes:
            r += d.output(self)

        r += tmpl.typecheck_else.format(
            name = self.main_type.type.canon_name)

        return r



class Output:
    def __init__(self,code,header,conv):
        self.cpp = code
        self.h = header
        self.conv = conv



class InitDef:
    pass

def check_getsetint(name,x,which):
    if not isinstance(x,gccxml.CPPMethod):
        raise SpecificationError('"{0}" is not a method'.format(name))

    # maybe this should be allowed
    if x.static:
        raise SpecificationError('"{0}" cannot be used as a {1} because it is static'.format(name,'setter' if which == SETTER else 'getter'))

class PropertyDef:
    doc = None
    def output(self,classdef,classint,cppint,conv):
        r = ""
        if self.get:
            f = classint.find(self.get.func)
            check_getsetint(self.get.func,f,GETTER)

            if f.returns == conv.void:
                raise SpecificationError('"{0}" must return a value'.format(self.get.func))

            if mandatory_args(f):
                raise SpecificationError('"{0}" should not require any arguments'.format(self.get.func))

            r = tmpl.property_get.render(
                cname = classdef.name,
                ctype = classdef.type,
                name = self.name,
                checkinit = True,
                code = conv.topy(f.returns,self.get.retsemantic).format("self->base.{0}()".format(self.get.func)))

        if self.set:
            f = classint.find(self.set.func)
            check_getsetint(self.get.func,f,SETTER)

            if can_accep(f,1):
                raise SpecificationError('"{0}" must take exactly one argument'.format(self.set.func))

            r += tmpl.property_set.render(
                cname = classdef.name,
                ctype = classdef.type,
                name = self.name,
                checkinit = True,
                cppfunc = self.set.func,
                code = conv.frompy(f.args[0].type)[0].format('value'))

        return r

    def table_entry(self,classdef):
        funccast = 'reinterpret_cast<{{0}}ter>(&obj_{cname}_{{0}}{name})'.format(
            cname = classdef.name,
            name = self.name)

        return '{{"{name}",{getter},{setter},{doc},0}}'.format(
            name = self.name,
            getter = funccast.format("get") if self.get else "0",
            setter = funccast.format("set") if self.set else "0",
            doc = tmpl.quote_c(self.doc) if self.doc else "0")

class MemberDef:
    doc = None

    def table_entry(self,classdef,classint,conv):
        m = classint.find(self.cmember)[0]
        if not isinstance(m,gccxml.CPPField):
            raise SpecificationError('"{0}" is not a member variable'.format(self.cmember))

        # TODO: offsetof is not relaiable for non-POD types (with g++, it will fail for classes with diamond virtual inheritance). A better solution is needed.
        # TODO: Don't even allow this when the type has an exposed subclass with multiple-inheritance. Create get/set methods instead.
        return '{{const_cast<char*>("{name}"),{type},offsetof(obj_{classdefname},base) + offsetof({classname},{mname}),{flags},{doc}}}'.format(
            name = self.name,
            type = conv.member_macro(m.type),
            classdefname = classdef.name,
            classname = classint.name,
            mname = self.cmember,
            flags = 'READONLY' if self.readonly else '0',
            doc = 'const_cast<char*>({0})'.format(tmpl.quote_c(self.doc)) if self.doc else '0')

class GetSetDef:
    def __init__(self,func,retsemantic = None):
        self.func = func
        self.retsemantic = retsemantic




class Tab:
    """Yield 4 x self.amount whitespace characters when converted to a string.

    An instance can be added to or subtracted from directly, to add to or subtract from "amount".

    """
    def __init__(self,amount = 1):
        if isinstance(amount,Tab):
            self.amount = amount.amount # copy constructor
        else:
            self.amount = amount

    def __str__(self):
        return self.amount * 4 * ' '

    def __unicode__(self):
        return self.amount * 4 * u' '

    def __repr__(self):
        return 'Tab({0})'.format(self.amount)

    # in-place addition/subtraction omitted to prevent modification when passed as an argument to a function

    def __add__(self,val):
        if isinstance(val,unicode):
            return self.__unicode__() + val
        if isinstance(val,str):
            return self.__str__() + val
        return Tab(self.amount + val)

    def __radd__(self,val):
        if isinstance(val,unicode):
            return val + self.__unicode__()
        if isinstance(val,str):
            return val + self.__str__()
        return Tab(self.amount + val)

    def __sub__(self,val):
        return Tab(self.amount - val)

    def line(self,x):
        return self.__unicode__() + x + u'\n' if isinstance(x,unicode) else self.__str__() + x + '\n'


class Overload:
    def __init__(self,func,retsemantic=None,args=None):
        self.func = func
        self.retsemantic = retsemantic
        self.args = args

class DefDef:
    def __init__(self,name,doc = None):
        self.name = name
        self.doc = doc
        self.overloads = []


class TypedOverload:
    def __init__(self,func,overload):
        self.func = func
        self.retsemantic = overload.retsemantic

class TypedDefDef:
    def __init__(self,scope,defdef):
        self.name = defdef.name
        self.doc = defdef.doc
        self.overloads = []

        for ov in defdef.overloads:
            cf = scope.find(ov.func)

            # if the first one is a function, they all should be functions
            if not isinstance(cf[0],(gccxml.CPPFunction,gccxml.CPPMethod)):
                raise SpecificationError('"{0}" is not a function or method'.format(cf[0]))
            assert all(isinstance(f,(gccxml.CPPFunction,gccxml.CPPMethod)) for f in cf)

            self.overloads.extend(TypedOverload(f,ov) for f in cf if (not ov.args) or same_args(ov.args,f.args))

    def call_code(self,conv,ov,var):
        code = ov.func.canon_name
        if isinstance(ov.func,gccxml.CPPMethod):
            if ov.func.static:
                code = ov.func.full_name()
            else:
                assert var
                code = var + '.' + code
        code += '({0})'

        return Conversion.Func(code + '; Py_RETURN_NONE;',True) if ov.func.returns == conv.void else \
            Conversion.Func('return {0};'.format(conv.topy(ov.func.returns,ov.retsemantic).format(code)),True)

    def make_argss(self,conv,var):
        return [(self.call_code(conv,ov,var),ov.func.args) for ov in self.overloads]

    def function_call(self,conv,var,use_kwds):
        return conv.function_call(self.make_argss(conv,var),use_kwds = use_kwds)

    def function_call_1arg(self,conv,var,ind=Tab(2)):
        return conv.function_call_1arg(self.make_argss(conv,var),ind=ind)

    def function_call_1arg_fallthrough(self,conv,var,ind=Tab(2)):
        return conv.function_call_1arg_fallthrough(self.make_argss(conv,var),ind)

    def _output(self,conv,prolog,type_extra,selfvar,funcnameprefix,objvar = None):
        arglens = [len(ov.func.args) for ov in self.overloads]
        maxargs = max(len(ov.func.args) for ov in self.overloads)
        minargs = min(mandatory_args(ov.func) for ov in self.overloads)

        if maxargs == 0:
            assert len(self.overloads) == 1
            type = 'METH_NOARGS'
            funcargs = ',PyObject *'
            code = Tab().line(self.call_code(conv,self.overloads[0],objvar).call.format(''))
        elif maxargs == 1 and minargs == 1 and not self.overloads[0].func.args[0].name:
            type = 'METH_O'
            funcargs = ',PyObject *arg'
            code = self.function_call_1arg(conv,objvar)
        elif len(self.overloads) == 1 and any(a.name for a in self.overloads[0].func.args): # is there a named argument?
            type = 'METH_VARARGS|METH_KEYWORDS'
            funcargs = ',PyObject *args,PyObject *kwds'
            code = self.function_call(conv,objvar,True)
        else:
            type = 'METH_VARARGS'
            funcargs = ',PyObject *args'
            code = self.function_call(conv,objvar,False)


        funcbody = tmpl.function.format(
            funcnameprefix = funcnameprefix,
            prolog = prolog,
            selfvar = selfvar,
            name = self.name,
            args = funcargs,
            code = code)

        tableentry = '{{"{name}",reinterpret_cast<PyCFunction>({funcnameprefix}{name}),{type}{typeextra},{doc}}}'.format(
            funcnameprefix = funcnameprefix,
            name = self.name,
            type = type,
            typeextra = type_extra,
            doc = tmpl.quote_c(self.doc) if self.doc else '0')

        return tableentry,funcbody

    def output(self,conv):
        return self._output(conv,'','','PyObject*','func_')




class SpecialFunc:
    def __init__(self,argtype,rettype = SF_RET_OBJ):
        self.argtype = argtype
        self.rettype = rettype
        self.func = None

    def set_func(self,func):
        self.func = func

    def check_static(self,cfunc):
        if not cfunc.static:
            raise SpecificationError('"{0}" must be static'.format(cfunc.canon_name))

    def check_integer_first(self,cfunc,conv):
        if not self.func.args[0].type in conv.integers:
            raise SpecificationError('The first argument to "{0}" must be an integer type'.format(cfunc.canon_name))

    def check_args_ret(self,cfunc,conv):
        if self.argtype <= SF_TWO_ARGS:
            can_accept(cfunc,self.argtype-1)
        elif self.argtype == SF_COERCE_ARGS:
            # no conversion is done
            can_accept(cfunc,2)
            t = cptr(cptr(gccxml.CPPBasicType('PyObject')))
            if cfunc.args[0].type != t or cfunc.args[1].type != t:
                raise SpecificationError('"{0}" must accept 2 arguments of PyObject**'.format(cfunc.canon_name))
            self.check_static(cfunc)
        elif self.argtype == SF_SSIZE_ARG:
            can_accept(cfunc,1)
            self.check_integer_first(cfunc,conv)
        elif self.argtype == SSIZE_OBJ_ARGS:
            can_accept(cfunc,2)
            self.check_integer_first(cfunc,conv)
        elif self.argtype == SF_TYPE_KEYWORD_ARGS:
            # no conversion is done for the first arg
            if len(cfunc.args) == 0:
                raise Specification('"{0}" must accept at least one argument'.format(cfunc.canon_name))
            if cfunc.args[0].type != cptr(gccxml.CPPBasicType('PyTypeObject')):
                raise Specification('The first argument to "{0}" must be PyTypeObject*'.format(cfunc.canon_name))
            self.check_static()


        if self.rettype != SF_RET_OB:
            if not self.func.returns in conv.integers:
                raise SpecificationError('"{0}" must return an integer type'.format(self.func))



class Disallowed:
    def __init__(self,reason):
        self.reason = reason

    def set_func(self,func):
        raise SpecificationError(self.reason)

    @property
    def func(self):
        return None

class ClassDef:
    def __init__(self):
        self.constructors = []
        self.methods = {}
        self.properties = []
        self.vars = []
        self.doc = None

        self.special_methods = {
            '__new__':          SpecialFunc(SF_KEYWORD_ARGS), # tp_new
            '__init__':         Disallowed('<def> cannot be used to implement __init__. Use <init> instead.'), # tp_init
            '__del__':          Disallowed('<def> cannot be used to implement __del__. It is used to call the destructor.'), # tp_dealloc
            '__repr__':         SpecialFunc(SF_NO_ARGS), # tp_repr
            '__str__':          SpecialFunc(SF_NO_ARGS), # tp_str
            '__lt__':           SpecialFunc(SF_ONE_ARG), # tp_richcompare
            '__le__':           SpecialFunc(SF_ONE_ARG), # tp_richcompare
            '__eq__':           SpecialFunc(SF_ONE_ARG), # tp_richcompare
            '__ne__':           SpecialFunc(SF_ONE_ARG), # tp_richcompare
            '__gt__':           SpecialFunc(SF_ONE_ARG), # tp_richcompare
            '__ge__':           SpecialFunc(SF_ONE_ARG), # tp_richcompare
            '__cmp__':          SpecialFunc(SF_ONE_ARG,SF_RET_INT), # tp_compare
            '__hash__':         SpecialFunc(SF_NO_ARGS,SF_RET_LONG), # tp_hash
            '__nonzero__':      SpecialFunc(SF_NO_ARGS,SF_RET_INT), # tp_as_number.nb_nonzero
            '__getattr__':      SpecialFunc(SF_ONE_ARG), # tp_getattro
            '__setattr__':      SpecialFunc(SF_TWO_ARGS), # tp_setattro
            '__get__':          SpecialFunc(SF_TWO_ARGS), # tp_descr_get
            '__set__':          SpecialFunc(SF_TWO_ARGS,SF_RET_INT), # tp_descr_set
            '__call__':         SpecialFunc(SF_KEYWORD_ARGS), # tp_call
            '__iter__':         SpecialFunc(SF_NO_ARGS), # tp_iter
            'next':             SpecialFunc(SF_NO_ARGS), # tp_iternext
            '__contains__':     SpecialFunc(SF_ONE_ARG,SF_RET_INT), # tp_as_sequence.sq_contains(NULL)
            '__add__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_add
            '__sub__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_subtract
            '__mul__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_multiply
            '__floordiv__':     SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_floor_divide
            '__mod__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_remainder
            '__divmod__':       SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_divmod
            '__pow__':          SpecialFunc(SF_TWO_ARGS), # tp_as_number.nb_power
            '__lshift__':       SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_lshift
            '__rshift__':       SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_rshift
            '__and__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_and
            '__xor__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_xor
            '__or__':           SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_or
            '__div__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_divide
            '__truediv__':      SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_true_divide
            '__iadd__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_add
            '__isub__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_subtract
            '__imul__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_multiply
            '__idiv__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_divide
            '__itruediv__':     SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_true_divide
            '__ifloordiv__':    SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_floor_divide
            '__imod__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_remainder
            '__ipow__':         SpecialFunc(SF_TWO_ARGS), # tp_as_number.nb_inplace_power
            '__ilshift__':      SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_lshift
            '__irshift__':      SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_rshift
            '__iand__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_and
            '__ixor__':         SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_xor
            '__ior__':          SpecialFunc(SF_ONE_ARG), # tp_as_number.nb_inplace_or
            '__neg__':          SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_negative
            '__pos__':          SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_positive
            '__abs__':          SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_absolute
            '__invert__':       SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_invert
            '__int__':          SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_int
            '__long__':         SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_long
            '__float__':        SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_float
            '__oct__':          SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_oct
            '__hex__':          SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_hex
            '__index__':        SpecialFunc(SF_NO_ARGS), # tp_as_number.nb_index
            '__coerce__':       SpecialFunc(SF_COERCE_ARGS,SF_RET_INT), # tp_as_number.nb_coerce

            # made-up names for special functions that don't have a distinct equivalent in Python
            '__concat__':       SpecialFunc(SF_ONE_ARG), # tp_as_sequence.sq_concat
            '__iconcat__':      SpecialFunc(SF_ONE_ARG), # tp_as_sequence.sq_inplace_concat
            '__repeat__':       SpecialFunc(SF_SSIZE_ARG), # tp_as_sequence.sq_repeat
            '__irepeat__':      SpecialFunc(SF_SSIZE_ARG), # tp_as_sequence.sq_inplace_repeat
            '__mapping__len__':   SpecialFunc(SF_NO_ARGS,SF_RET_SSIZE), # tp_as_mapping.mp_length(NULL)
            '__sequence__len__':  SpecialFunc(SF_NO_ARGS,SF_RET_SSIZE), # tp_as_sequence.sq_length
            '__mapping__getitem__': SpecialFunc(SF_ONE_ARG), # tp_as_mapping.mp_subscript(NULL)
            '__sequence__getitem__': SpecialFunc(SF_SSIZE_ARG), # tp_as_sequence.sq_item(NULL)
            '__mapping__setitem__': SpecialFunc(SF_TWO_ARGS), # tp_as_mapping.mp_ass_subscript(NULL)
            '__sequence__setitem__': SpecialFunc(SF_SSIZE_OBJ_ARGS) # tp_as_sequence.sq_ass_item(NULL)

        }

        for alias,key in [
                ('<','__lt__'),
                ('<=','__le__'),
                ('==','__eq__'),
                ('!=','__ne__'),
                ('>','__gt__'),
                ('>=','__ge__'),
                ('()','__call__'),
                ('+','__add__'),
                ('+=','__iadd__'),
                ('-','__sub__'),
                ('-=','__isub__'),
                ('*','__mul__'),
                ('*=','__imul__'),
                ('**','__pow__'),
                ('**=','__ipow__'),
                ('/','__div__'),
                ('/=','__idiv__'),
                ('//','__floordiv__'),
                ('//=','__ifloordiv__'),
                ('<<','__lshift__'),
                ('<<=','__ilshift__'),
                ('>>','__rshift__'),
                ('>>=','__irshift__'),
                ('&','__and__'),
                ('&=','__iand__'),
                ('^','__xor__'),
                ('^','__ixor__'),
                ('|','__or__'),
                ('|=','__ior__'),
                ('~','__invert__')]:
            self.special_methods[alias] = self.special_methods[key]


class TypedClassDef:
    def __init__(self,scope,classdef):
        self.name = classdef.name
        self.type = scope.find(classdef.type)[0]
        if not isinstance(self.type,gccxml.CPPClass):
            raise SpecificationError('"{0}" is not a struct/class type'.format(classdef.type))

        self.constructors = classdef.constructors
        self.methods = [TypedDefDef(self.type,dd) for dd in classdef.methods.itervalues()]

        self.special_methods = {}
        for name,m in classdef.special_methods.iteritems():
            if isinstance(m,SpecialFunc) and (name[0] == '_' or name[0] == 'n'): # skip the aliases
                new = copy.copy(m)
                if new.func:
                    new.func = TypedDefDef(self.type,new.func)
                self.special_methods[name] = new

        self.properties = classdef.properties
        self.vars = classdef.vars
        self.doc = classdef.doc

        self.bases = []
        self.derived = []
        self.features = ObjFeatures()

        for m in self.methods:
            if any(ov.func.static != m.overloads[0].func.static for ov in m.overloads):
                raise SpecificationError('The function overloads must be either be all static or all non-static',method=m.name)

    def basecount(self):
        return sum(1 + b.basecount() for b in self.bases)

    @property
    def dynamic(self):
        return len(self.bases) > 1

    # a seperate property in case a dynamic declration is ever needed for a single/no-inheritance class
    multi_inherit = dynamic

    @property
    def static_from_dynamic(self):
        return len(self.bases) == 1 and self.bases[0].dynamic

    def has_multi_inherit_subclass(self):
        return any(c.multi_inherit or c.has_multi_inherit_subclass() for c in self.derived)

    def findbases(self,classdefs):
        assert len(self.bases) == 0
        for b in self.type.bases:
            cd = classdefs.get(b.type)
            if cd:
                self.bases.append(cd)
                cd.derived.append(self)

    def cast_base_func(self):
        return tmpl.cast_base.render(
            type = self.type.typestr(),
            name = self.name,
            canholdref = self.features.managed_ref)

    def get_base_func(self):
        if self.has_multi_inherit_subclass():
            return self.heirarchy_chain().downcast_func(self.features)
        else:
            return tmpl.get_base.format(
                type = self.type.typestr(),
                name = self.name)

    def __repr__(self):
        return '<TypedClassDef: {0}>'.format(self.name)

    def _heirarchy_chain(self,node):
        if self.multi_inherit:
            node = node.new_node(self)
        else:
            node.append_single(self)

        for c in self.derived:
            c._heirarchy_chain(node)

    def heirarchy_chain(self):
        """Return a tree of lists of derived classes divided at classes with
        multiple inheritance.

        The tree is the result when you take the tree containing all direct and
        indirect derived classes of this class, plus this class, then combine
        each class that does not inherit from more than one class (any class,
        not necessarily from this tree), with its base class so each node has a
        list that either starts with this class, or a class the inherits from
        more than one class.

        If there are no classes with multiple inheritance, the result will be a
        single node.

        """
        node = MultiInheritNode(self)
        for c in self.derived:
            c._heirarchy_chain(node)

        return node

    def have_special(self,*keys):
        return any(self.special_methods[k].func for k in keys)

    def rich_compare(self,out,need_cast):
        if not self.have_special('__lt__','__le__','__eq__','__ne__','__gt__','__ge__'):
            return False

        print >> out.cpp, tmpl.richcompare_start.format(
            name = self.name,
            prolog = self.method_prolog(need_cast))

        for f,c in [
                ('__lt__','Py_LT'),
                ('__le__','Py_LE'),
                ('__eq__','Py_EQ'),
                ('__ne__','Py_NE'),
                ('__gt__','Py_GT'),
                ('__ge__','Py_GE')]:
            code = ''
            sf = self.special_methods[f]
            if sf.func:
                code = sf.func.function_call_1arg_fallthrough(out.conv,'base',Tab(3))

            print >> out.cpp, tmpl.richcompare_op.format(op = c,code = code)

        print >> out.cpp, tmpl.richcompare_end

        return True

    def have_number(self):
        return self.have_special('__nonzero__','__add__','__sub__','__mul__',
            '__floordiv__','__mod__','__divmod__','__pow__','__lshift__',
            '__rshift__','__and__','__xor__','__or__','__div__','__truediv__',
            '__iadd__','__isub__','__imul__','__idiv__','__itruediv__',
            '__ifloordiv__','__imod__','__ipow__','__ilshift__','__irshift__',
            '__iand__','__ixor__','__ior__','__neg__','__pos__','__abs__',
            '__invert__','__int__','__long__','__float__','__oct__','__hex__',
            '__index__','__coerce__')

    def have_sequence(self):
        return self.have_special('__sequence__len__','__sequence__getitem__',
            '__sequence__setitem__','__contains__','__concat__','__iconcat__',
            '__repeat__','__irepeat__')

    def have_mapping(self):
        return self.have_special('__mapping__len__','__mapping__getitem__','__mapping__setitem__')

    def method_prolog(self,has_mi_subclass):
        return tmpl.method_prolog.render(
            type = self.type.canon_name,
            name = self.name,
            needcast = has_mi_subclass)

    def method(self,m,classint,conv,need_cast):
        prolog = ''
        type_extra = ''

        if m.overloads[0].func.static:
            type_extra = '|METH_STATIC'
        else:
            prolog = self.method_prolog(need_cast)

        return m._output(conv,prolog,type_extra,'obj_{0} *self'.format(self.name),'obj_{0}_method_'.format(self.name),'base')

    def output(self,out,module):
        has_mi_subclass = self.has_multi_inherit_subclass()

        # If this is a statically declared class and its base is a dynamic
        # class, don't set tp_base yet (we don't have an address for it yet).
        bases = []
        if not self.static_from_dynamic:
            if self.bases:
                bases = map((lambda x: 'get_obj_{0}Type()'.format(x.name)), self.bases)
            elif has_mi_subclass:
                # common type needed for multiple inheritance
                bases = ['&obj__CommonType']


        print >> out.h, tmpl.classdef.render(
            name = self.name,
            type = self.type.canon_name,
            checkinit = True,
            dynamic = self.dynamic,
            canholdref = self.features.managed_ref,
            constructors = ({
                'args' : ','.join('{0!s} _{1}'.format(a,i) for i,a in enumerate(m.args)),
                'argvals' : ','.join('_{0}'.format(i) for i in xrange(len(m.args)))}
                    for m in self.type.members if isinstance(m,gccxml.CPPConstructor) and not varargs(m))),

        richcompare = self.rich_compare(out,has_mi_subclass)

        destructref = False
        initdestruct = False
        d = self.type.getDestructor()
        if d:
            print >> out.cpp, tmpl.destruct.render(
                name = self.name,
                type = self.type.canon_name,
                canholdref = self.features.managed_ref),
            destructref = True
            initdestruct = True


        getsetref = False
        if self.properties:
            for p in self.properties:
                print >> out.cpp, p.output(self,self.type,cppint,out.conv),

            print >> out.cpp,  tmpl.getset_table.format(
                name = self.name,
                items = ',\n    '.join(p.table_entry(self) for p in self.properties)),

            getsetref = True

        membersref = False
        if self.vars:
            print >> out.cpp, tmpl.member_table.format(
                name = self.name,
                items = ',\n    '.join(v.table_entry(self,self.type,out.conv) for v in self.vars)),
            membersref = True


        methodsref = False
        if self.methods:
            tentries,bodies = zip(*[self.method(m,self.type,out.conv,has_mi_subclass) for m in self.methods])
            for b in bodies:
                print >> out.cpp, b,

            print >> out.cpp, tmpl.method_table.format(
                name = self.name,
                items = ',\n    '.join(tentries)),

            methodsref = True


        func = Conversion.Func('new(&self->base) {0}({{0}});'.format(self.type.canon_name),False)
        if self.constructors:
            if self.constructors[0].args is None:
                # no overload specified means use all constructors

                assert len(self.constructors) == 1
                cons = [(func,con.args) for con in self.type.members if isinstance(con,gccxml.CPPConstructor)]
            else:
                cons = [(func,self.type.getConstructor(con.args).args) for con in self.constructors]
        else:
            cons = [(func,self.type.getConstructor().args)]

        cons = out.conv.function_call(cons,'-1',True)

        print >> out.cpp, tmpl.classtypedef.render(
            dynamic = self.dynamic,
            name = self.name,
            type = self.type.canon_name,
            initdestruct = initdestruct,
            initcode = cons,
            module = module,
            destructref = destructref,
            doc = self.doc,
            getsetref = getsetref,
            membersref = membersref,
            methodsref = methodsref,
            richcompare = richcompare,
            bases = bases),


class cppcode:
    def __init__(self,val,prep = ''):
        self.val = val
        self.prep = prep

    def __str__(self):
        return self.prep + self.val

def base_count(x):
    """Return the number of direct and indirect base classes of x."""
    return sum(base_count(b.type) for b in x.bases)

def cconst(x):
    return gccxml.CPPCvQualifiedType(x,True)

def cptr(x):
    return gccxml.CPPPointerType(x)

def strip_cvq(x):
    return x.type if isinstance(x,gccxml.CPPCvQualifiedType) else x

def strip_refptr(x):
    return strip_cvq(x.type if isinstance(x,(gccxml.CPPPointerType,gccxml.CPPReferenceType)) else x)

def const_qualified(x):
    """This does NOT test for the "restrict" qualifier (because the code that this program generates never aliases mutable pointers)."""
    return isinstance(x,gccxml.CPPCvQualifiedType) and x.const and not x.volatile


class ArgBranchNode:
    def __init__(self):
        self.basic = dict.fromkeys(TYPES_LIST)
        self.objects = []

        # an overloaded function is available if and only if self.call is not None
        self.call = None

    def child_nodes(self):
        return itertools.chain(filter(None,self.basic.itervalues()),(val for k,val in self.objects))

    def min_arg_length(self):
        if self.call:
            return 0

        length = sys.maxint

        for n in self.child_nodes():
            length = min(length,n.min_arg_length()+1)

        assert length < sys.maxint
        return length

    def max_arg_length(self):
        length = 0

        for n in self.child_nodes():
            length = max(length,n.max_arg_length()+1)

        return length

    def merge(self,b):
        if b:
            if b.call:
                if self.call:
                    raise SpecificationError(
                        'Ambiguous overloads: Overload accepting "{0!s}" and overload with "{1!s}" translate to the same set of Python Arguments.'
                            .format(b.call,self.call))

                self.call = b.call

            for t in TYPES_LIST:
                if b.basic[t]:
                    self.basic[t] = b.basic[t].merge(self.basic[t])

            otherobj = dict(b.objects)
            for k,val in self.objects:
                val.merge(otherobj.pop(k,None))
            self.objects.extend(otherobj.iteritems())

        return self

    def sort_objects(self):
        """Sort self.objects on this instance and all child instances so that no
        CPPClass is preceded by its base class.

        When comparing types, if S inherits from B, and our type T matches S,
        then T will always match B, so S must be tested first, since the tests
        will stop after finding the first viable match.

        """
        self.objects.sort(key = (lambda x: base_count(strip_refptr(x[0]))),reverse = True)
        for n in self.child_nodes(): n.sort_objects()

    def basic_and_objects_code(self,conv,argconv,skipsize,ind,get_arg,exactlenchecked = False):
        r = ''

        # check for general classes
        if self.objects:
            for t,branch in self.objects:
                check,cast = conv.check_and_cast(t)
                r += '{0}if({1}) {{\n{2}{0}}}\n'.format(
                    ind,
                    check.format(get_arg(len(argconv))),
                    branch.get_code(conv,argconv + [cast],skipsize,ind + 1,get_arg,exactlenchecked))


        # check for numeric types
        nums = 0
        if self.basic[TYPE_FLOAT]: nums |= CHECK_FLOAT
        if self.basic[TYPE_INT]: nums |= CHECK_INT
        if self.basic[TYPE_LONG]: nums |= CHECK_LONG
        if nums:
            for c,t in zip(coercion[nums],[TYPE_FLOAT,TYPE_INT,TYPE_LONG]):
                if c:
                    r += '{0}if({2}({1})) {{\n{3}{0}}}\n'.format(
                        ind,
                        get_arg(len(argconv)),
                        c,
                        self.basic[t].get_code(conv,argconv + [None],skipsize,ind + 1,get_arg,exactlenchecked))


        # check for string types
        if self.basic[TYPE_UNICODE]:
            r += '{0}if(PyUnicode_Check({1}){2}) {{\n{3}{0}}}\n'.format(
                ind,
                get_arg(len(argconv)),
                '' if self.basic[TYPE_STR] else ' && PyString_Check(o)',
                self.basic[TYPE_UNICODE].get_code(conv,argconv + [None],skipsize,ind + 1,get_arg,exactlenchecked))

        if self.basic[TYPE_STR]:
            r += '{0}if(PyString_Check({1})) {{\n{2}{0}}}\n'.format(
                ind,
                get_arg(len(argconv)),
                self.basic[TYPE_STR].get_code(conv,argconv + [None],skipsize,ind + 1,get_arg,exactlenchecked))

        return r

    def call_code(self,conv,argconv,ind,get_arg):
        func,args = self.call

        r = ind.line(
            func.call.format('\n    {0}{1}'.format(ind,
                ',\n{0}    '.format(ind).join(
                    (c or conv.frompy(a.type)[0]).format(get_arg(i)) for i,a,c in zip(itertools.count(),args,argconv)))))
        if not func.returns:
            r += ind.line('goto end;')

        return r

    def get_code(self,conv,argconv = [],skipsize = 0,ind = Tab(2),get_arg = lambda x: 'PyTuple_GET_ITEM(args,{0})'.format(x),exactlenchecked = False):
        anychildnodes = any(self.basic.itervalues()) or self.objects

        assert anychildnodes or self.call

        r = ''
        get_size = ind.line('if(PyTuple_GET_SIZE(args) {1} {2}) {{')

        if skipsize > 0:
            assert anychildnodes

            r += self.basic_and_objects_code(conv,argconv,skipsize-1,ind,get_arg,exactlenchecked)
            if self.call:
                r += self.call_code(conv,argconv,ind,get_arg)

        elif anychildnodes:
            # if the exact length was tested, "skipsize" should cover the rest of the arguments
            assert not exactlenchecked

            min_args = self.min_arg_length()
            max_args = self.max_arg_length()

            if min_args == max_args:
                r += get_size.format(ind,'==',len(argconv) + min_args)
                ind += 1

                r += self.basic_and_objects_code(conv,argconv,min_args - 1,ind,get_arg,True)
                if self.call:
                    r += self.call_code(conv,argconv,ind,get_arg)

                ind -= 1
                r += ind.line('}')
            else:
                r += get_size.format(ind,'>',len(argconv))

                r += self.basic_and_objects_code(conv,argconv,min_args - 1,ind + 1,get_arg)

                if self.call:
                    r += ind.line('} else {')
                    r += self.call_code(conv,argconv,ind+1,get_arg)

                r += ind.line('}')

        elif exactlenchecked:
            assert self.call
            r += self.call_code(conv,argconv,ind,get_arg)

        else:
            assert self.call
            r += get_size.format(ind,'==',len(argconv))
            r += self.call_code(conv,argconv,ind + 1,get_arg)
            r += ind.line('}')

        return r





class Conversion:
    class Func:
        def __init__(self,call,returns):
            """
            call -- a format string with {0}
            returns -- whether or not the expression returns from the caller
            """
            self.call = call
            self.returns = returns

    def __init__(self,tns):
        # get the types specified by the typedefs
        for x in ("bool","sint","uint","sshort","ushort","slong","ulong",
                  "float","double","long_double","size_t","py_size_t","schar",
                  "uchar","char","wchar_t","py_unicode","void","stdstring",
                  "stdwstring"):
            setattr(self,x,tns.find("type_"+x)[0])

        try:
            for x in ("slonglong","ulonglong"):
                setattr(self,x,tns.find("type_"+x)[0])
        except SpecificationError:
            self.slonglong = None
            self.ulonglong = None


        self.cstring = cptr(cconst(self.char))
        self.cmutstring = cptr(self.char)
        self.cwstring = cptr(cconst(self.wchar_t))
        self.pyobject = cptr(gccxml.CPPBasicType("PyObject"))


        fl = "PyInt_FromLong({0})"
        ful = "PyLong_FromUnsignedLong({0})"
        fd = "PyFloat_FromDouble({0})"

        self.__topy = {
            self.bool : 'bool_to_py({0})',
            self.sshort : fl,
            self.ushort : fl,
            self.sint : fl,
            self.uint : ful if self.uint.size == self.ulong.size else fl,
            self.slong : fl,
            self.ulong : ful,
            self.float : fd,
            self.double : fd,
            self.long_double : 'PyFloat_FromDouble(static_cast<double>({0}))',
            self.size_t : 'PyLong_FromSize_t({0})',
            self.pyobject : '{0}',
            self.stdstring : 'StringToPy({0})',
            self.cstring : 'PyString_FromString({0})'
        }

        self.basic_types = {
            TYPE_INT : set([self.sshort,self.ushort,self.sint,self.uint,self.slong]),
            TYPE_LONG : set([self.ulong,self.size_t]),
            TYPE_FLOAT : set([self.float,self.double,self.long_double]),
            TYPE_STR : set([self.cstring,self.stdstring]),
            TYPE_UNICODE : set([self.cwstring,self.stdwstring])
        }

        self.basic_types[TYPE_LONG if self.uint.size == self.ulong.size else TYPE_INT].add(self.slong)


        tod = (False,'PyToDouble({0})')
        # The first value of each tuple specifies whether the converted type is
        # a reference to the original value. If not, it cannot be passed by
        # non-const reference.
        self.__frompy = {
            self.bool : (False,'static_cast<bool>(PyObject_IsTrue({0}))'),
            self.sshort : (False,'PyToShort({0})'),
            self.ushort : (False,'PyToUShort({0})'),
            self.sint : (False,'PyToInt({0})'),
            self.uint : (False,'PyToUInt({0})'),
            self.slong : (False,'PyToLong({0})'),
            self.ulong : (False,'PyToULong({0})'),
            self.float : (False,'static_cast<float>(PyToDouble({0}))'),
            self.double : tod,
            self.long_double : tod,
            self.cstring : (False,'PyString_AsString({0})')
        }

        ts = 'T_STRING'
        self.__pymember = {
            self.sshort : 'T_SHORT',
            self.ushort : 'T_USHORT',
            self.sint : 'T_INT',
            self.uint : 'T_UINT',
            self.slong : 'T_LONG',
            self.float : 'T_FLOAT',
            self.double : 'T_DOUBLE',
            self.schar : 'T_BYTE',
            self.uchar : 'T_UBTYE',
            self.pyobject : 'T_OBJECT_EX',
            self.cstring : ts,
            self.cmutstring : ts,
            gccxml.CPPBasicType('Py_ssize_t') : 'T_PYSSIZET'
        }

        if self.slonglong:
            self.__topy[self.slonglong] = 'PyLong_FromLongLong({0})'
            self.__topy[self.ulonglong] = 'PyLong_FromUnsignedLongLong({0})'

            self.basic_types[TYPE_LONG].add(self.slonglong)
            self.basic_types[TYPE_LONG].add(self.ulonglong)

            self.__frompy[self.slonglong] = (False,'PyToLongLong({0})')
            self.__frompy[self.ulonglong] = (False,'PyToULongLong({0})')

            self.__pymember[self.slonglong] = 'T_LONGLONG'
            self.__pymember[self.ulonglong] = 'T_ULONGLONG'

        self.cppclasstopy = {}

        self.integers = set((self.sint,self.uint,self.sshort,self.ushort,
            self.slong,self.ulong,self.size_t,self.py_size_t,self.schar,
            self.uchar,self.char))

    def __topy_pointee(self,x):
        return self.__topy.get(strip_cvq(x.type))

    def topy(self,t,retsemantic = None):
        r = self.__topy.get(t)
        if r: return r

        if retsemantic == RET_COPY:
            if isinstance(t,gccxml.CPPReferenceType):
                r = self.__topy_pointee(t)
                if r: return r
            elif isinstance(t,gccxml.CPPPointerType):
                r = self.__topy_pointee(t)
                if r: return r.format('*({0})')

        raise SpecificationError('No conversion from "{0}" to "PyObject*" is registered'.format(t.typestr()))

    def frompy(self,t):
        '''Returns a tuple containing the conversion code string and the type (CPP_X_Type) that the code returns'''

        assert isinstance(t,gccxml.CPPType)

        r = self.__frompy.get(t)
        ref = lambda x: gccxml.CPPReferenceType(x) if r[0] else x
        if r: return r[1],ref(t)

        # check if t is a pointer or reference to a type we can convert
        if isinstance(t,gccxml.CPPReferenceType):
            nt = strip_cvq(t.type)
            r = self.__frompy.get(nt)
            if r and (r[0] or const_qualified(t.type)):
                return r[1], ref(nt)
        elif isinstance(t,gccxml.CPPPointerType):
            nt = strip_cvq(t.type)
            r = self.__frompy.get(nt)
            if r and(r[0] or const_qualified(t.type)):
                return '*({0})'.format(r[1]), ref(nt)
        elif isinstance(t,gccxml.CPPCvQualifiedType):
            r = self.__frompy.get(t.type)
            if r: return r[1], ref(t.type)

        raise SpecificationError('No conversion from "PyObject*" to "{0}" is registered'.format(t.typestr()))

    def member_macro(self,t):
        try:
            return self.__pymember[t]
        except LookupError:
            # TODO: this should be done automatically
            raise SpecificationError('A member of type "{0}" cannot be exposed directly. Define a getter and/or setter for this value instead.'.format(t.typestr()))

    def check_and_cast(self,t):
        cdef = self.cppclasstopy[strip_refptr(t)]
        check ='PyObject_TypeCheck({{0}},get_obj_{0}Type())'.format(cdef.name)
        cast = '{0}reinterpret_cast<obj_{1}*>({{0}})->base'.format('&' if isinstance(t,gccxml.CPPPointerType) else '',cdef.name)
        return check,cast

    def arg_parser(self,args,use_kwds = True,indent = Tab(2)):
        # even if we are not taking any arguments, get_arg::finish should still be called (to report an error if arguments were received)

        prep = '{0}get_arg ga(args,{1});\n'.format(indent,'kwds' if use_kwds else '0')

        if any(a.default for a in args):
            prep += indent.line('PyObject *temp;')

        for i,a in enumerate(args):
            frompy, frompytype = self.frompy(a.type)
            var = frompytype.typestr("_{0}".format(i))
            name = '"{0}"'.format(a.name) if a.name and use_kwds else '0'
            if a.default:
                prep += '{0}temp = ga({1},false);\n{0}{2} = temp ? {3} : {4};\n'.format(indent,name,var,frompy.format('temp'),a.defult)
            else:
                prep += '{0}{1} = {2};\n'.format(indent,var,frompy.format('ga({0},true)'.format(name)))

        prep += indent.line('ga.finished();')

        return cppcode(','.join('_{0}'.format(i) for i in range(len(args))), prep)

    def function_call(self,calls,errval = '0',use_kwds = True):
        """Generate code to call one function from a list of overloads.

        calls -- A sequence of tuples containing a function (Conversion.Func)
            and a list of arguments
        errval -- The value to return to signal an error
        use_kwds -- whether keyword arguments are available or not (does not
            apply if len(calls) is greater than 1)

        Caveat: only position arguments are checked, unless len(calls) == 1. Use
        of keywords will result in an exception being thrown.

        Caveat: the resulting algorithm for overload resolution is different
        from the C++ standard. It will compare one argument at a time and will
        stop after finding a viable match. The parallel arguments are sorted
        from most to least specific, however given classes S and B, where B is
        the base class of S, if there are two overloads S,B,B and B,S,S and the
        arguments given are S,S,S then S,B,B will be chosen because the first
        argument was a better match. The same limitation applies when S and B
        are built-in types that can be converted to one-another (unicode vs str
        and float vs int vs long).

        """
        assert calls

        if len(calls) == 1:
            code = self.arg_parser(calls[0][1],use_kwds)
            return code.prep + Tab(2).line(calls[0][0].call.format(code.val))

        # turn default values into overloads
        ovlds = []
        for f,args in calls:
            newargs = []
            for a in args:
                if a.default:
                    ovlds.append((f,newargs))
                    a = copy.copy(a)
                    a.default = None
                newargs.append(a)
            ovlds.append((f,args))

        return tmpl.overload_func_call.render(
            inner = self.generate_arg_tree(ovlds).get_code(self),
            nokwdscheck = use_kwds,
            args = 'args',
            errval = errval,
            endlabel = len(calls) > 1 and not all(c[0].returns for c in calls))

    def function_call_1arg_fallthrough(self,calls,ind=Tab(2)):
        assert calls
        return self.generate_arg_tree(calls).basic_and_objects_code(self,[],0,ind,(lambda x: 'arg'),True)

    def function_call_1arg(self,calls,errval='0',ind=Tab(2)):
        if len(calls) == 1:
            return ind + calls[0][0].call.format(self.frompy(calls[0][1][0].type)[0].format('arg'))

        return tmpl.overload_func_call.render(
            inner = self.function_call_1arg_fallthrough(calls,ind),
            nokwdscheck = False,
            args = 'arg',
            errval = errval,
            endlabel = False)

    def add_conv(self,t,to,from_):
        if to: self.__topy[t] = to
        if from_: self.__frompy[t] = from_

    def closest_type_is_pytype(self,t,py):
        s = self.basic_types[py]
        return (t in s) or isinstance(t,(gccxml.CPPPointerType,gccxml.CPPReferenceType)) and (strip_cvq(t.type) in s)

    def generate_arg_tree(self,calls):
        tree = self._generate_arg_tree([(x[1],x) for x in calls])
        tree.sort_objects()
        return tree

    def _generate_arg_tree(self,argss):
        argss.sort(key = lambda x: len(x[0]) and x[0][0].type.typestr())

        node = ArgBranchNode()
        for k,g in itertools.groupby(argss,lambda x: bool(x[0]) and x[0][0].type):
            if k:
                subnode = self._generate_arg_tree([(x[1:],orig) for x,orig in g])

                # see if the argument is any of the types that require special handling
                for t in TYPES_LIST:
                    if self.closest_type_is_pytype(k,t):
                        node.basic[t] = subnode.merge(node.basic[t])
                        break
                else:
                    node.objects.append((k,subnode))
            else:
                g = list(g)

                assert len(g) == 1
                assert not len(g[0][0])

                node.call = g[0][1]

        return node



class ModuleDef:
    def __init__(self):
        self.classes = []
        self.functions = {}
        self.doc = ''

    def print_gccxml_input(self,out):
        # In addition to the include files, declare certain typedefs so they can be matched against types used elsewhere
        print >> out, tmpl.gccxmlinput_start.format(self._formatted_includes(),TEST_NS)

        # declare a bunch of dummy functions with the arguments we want gccxml to parse for us
        for i,x in enumerate(self._funcs_with_overload()):
            print >> out, 'void dummy_func_{0}({1});\n'.format(i,x.args)

        print >> out, '}\n'

    def _collect_overload_arg_lists(self,tns):
        for i,x in enumerate(self._funcs_with_overload()):
            f = tns.find('dummy_func_{0}'.format(i))[0]
            assert isinstance(f,gccxml.CPPFunction)
            x.args = f.args

    def _formatted_includes(self):
        return "\n".join('#include "{0}"'.format(i) for i in self.includes)

    def write_file(self,path,scope):
        tns = scope.find(TEST_NS)[0]

        self._collect_overload_arg_lists(tns)

        conv = Conversion(tns)

        out = Output(
            open(os.path.join(path, self.name + '.cpp'),'w'),
            open(os.path.join(path, self.name + '.h'),'w'),
            conv)

        print >> out.cpp, tmpl.module_start.format(
            includes = self._formatted_includes(),
            module = self.name)

        print >> out.h, tmpl.header_start.format(module = self.name)


        classes = {}

        for cdef in self.classes:
            c = TypedClassDef(scope,cdef)
            classes[c.type] = c

            # these assume the class has copy constructors
            conv.add_conv(c.type,'new obj_{0}({{0}})'.format(c.name),(True,'get_base_{0}({{0}})'.format(c.name)))
            conv.cppclasstopy[c.type] = c

        for c in classes.itervalues():
            c.findbases(classes)

        # find all methods and functions that return objects that require special storage
        for c in classes.itervalues():
            for m in c.methods:
                for ov in m.overloads:
                    if ov.retsemantic == RET_MANAGED_REF and isinstance(ov.func.returns,(gccxml.CPPReferenceType,gccxml.CPPPointerType)):
                        t = strip_cvq(ov.func.returns.type)
                        retcdef = classes.get(t)
                        if not retcdef:
                            raise SpecificationError('return type of "{0}" is not an exposed type'.format(cm.name))
                        retcdef.features.managed_ref = True

        # Sort classes by heirarchy. Base classes need to be declared before derived classes.
        classes = sorted(classes.values(),key=TypedClassDef.basecount)

        for c in classes:
            print >> out.cpp, c.cast_base_func()

        for c in classes:
            print >> out.cpp, c.get_base_func()

        for c in classes:
            c.output(out,self.name)

        functable = []
        for f in self.functions.itervalues():
            tentry,body = TypedDefDef(scope,f).output(conv)
            print >> out.cpp, body
            functable.append(tentry)

        print >> out.cpp, tmpl.module.render(
            funclist = functable,
            module = self.name,
            doc = self.doc,
            classes = ({
                'name' : c.name,
                'dynamic' : c.dynamic,
                'base' : c.static_from_dynamic and c.bases[0].name}
                    for c in classes)
        )

        print >> out.h, tmpl.header_end

    def _funcs_with_overload(self):
        """yields function-like objects that have a non-empty overload defined"""

        for c in self.classes:
            for x in c.constructors:
                if x.args: yield x
            for m in c.methods.itervalues():
                for x in m.overloads:
                    if x.args: yield x

        for f in self.functions.itervalues():
            for x in f.overloads:
                if x.args: yield x


pykeywords = set((
    'and','as','assert','break','class','continue','def','del','elif','else',
    'except','exec','finally','for','from','global','if','import','in','is',
    'lambda','not','or','pass','print','raise','return','try','while','with',
    'yield'))

def get_valid_py_ident(x,backup):
    if x:
        m = re.match(r'[a-zA-Z_]\w*',x)
        if not (m and m.end() == len(x)):
            raise SpecificationError('"{0}" is not a valid Python indentifier')
        if x in pykeywords:
            raise SpecificationError('"{0}" is a reserved identifier')
        return x
    if backup in pykeywords:
        return backup + '_'
    return re.sub('\W','_',backup)


def _add_func(x,ref):
    cur = ref.func
    if cur:
        if x.doc:
            if cur.doc:
                raise SpecificationError("<doc> was defined twice for the same function/method")
            cur.doc = x.doc
        cur.overloads.extend(x.overloads)
    else:
        ref.set_func(x)

class DictRef:
    def __init__(self,dict,key):
        self.dict = dict
        self.key = key

    @property
    def func(self):
        return self.dict.get(self.key)

    def set_func(self,val):
        self.dict[self.key] = val


def add_func(funcs,x):
    _add_func(x,DictRef(funcs,x.name))

def add_func_sm(sm,x):
    _add_func(x,sm)



def stripsplit(x):
    return [i.strip() for i in x.split(',')]

class tag_Class(tag):
    def __init__(self,args):
        self.r = ClassDef()
        self.r.type = args["type"]
        self.r.name = get_valid_py_ident(args.get("name"),self.r.type)

    def child(self,name,data):
        if name == "init":
            self.r.constructors.append(data)
        elif name == "doc":
            self.r.doc = data
        elif name == "property":
            self.r.properties.append(data)
        elif name == 'member':
            self.r.vars.append(data)
        elif name == 'def':
            sm = self.r.special_methods.get(data.name)
            if sm:
                add_func_sm(sm,data)
            else:
                add_func(self.r.methods,data)

    def end(self):
        if len(self.r.constructors) > 1 and any(con.overload is None for con in self.r.constructors):
            s = SpecificationError('Omitting "overload" implies all overloads are to be exposed. Therefore only one occurance is allowed.')
            s.info['class'] = self.r.name
            raise s
        return self.r


class tag_Init(tag):
    def __init__(self,args):
        self.r = InitDef()
        # Don't parse the overload. gccxml will do that for us.
        self.r.args = args.get("overload")

class tag_Module(tag):
    def __init__(self,args):
        self.r = ModuleDef()
        self.r.name = args["name"]
        self.r.includes = stripsplit(args["include"])

    def child(self,name,data):
        if name == "class":
            self.r.classes.append(data)
        if name == "def":
            add_func(self.r.functions,data)
        elif name == "doc":
            self.r.doc = data

class tag_Doc(tag):
    def __init__(self,args):
        self.r = ""

    def text(self,data):
        self.r = textwrap.dedent(data)

def getset_or_none(x):
    return x and GetSetDef(x)

class tag_Property(tag):
    def __init__(self,args):
        self.r = PropertyDef()
        self.r.name = args["name"]
        self.r.get = getset_or_none(args.get("get"))
        self.r.set = getset_or_none(args.get("set"))

    def end(self):
        if not (self.r.get or self.r.set):
            raise SpecificationError("property defined with neither a getter nor a setter")
        return self.r

    def child(self,name,data):
        if name == "doc":
            self.r.doc = data
        elif name == "get":
            if self.r.get:
                raise SpecificationError("multiple getters defined for property")
            self.r.get = data
        elif name == "set":
            if self.r.set:
                raise SpecificationError("multiple setters defined for property")
            self.r.set = data

class tag_Member(tag):
    def __init__(self,args):
        self.r = MemberDef()
        self.r.cmember = args['cmember']
        self.r.name = get_valid_py_ident(args.get('name'),self.r.cmember)
        self.r.readonly = args.get('readonly',False)
        if self.r.readonly is not False:
            try:
                self.r.readonly = {'true':True,'false':False}[self.r.readonly.lower()]
            except LookupError:
                raise ParseError('The value of "readonly" must be either "true" or "false"')


def get_ret_semantic(args):
    rs = args.get("return-semantic")
    if rs is not None:
        mapping = {"copy" : RET_COPY, "managedref" : RET_MANAGED_REF, "default" : None}
        try:
            rs = mapping[rs]
        except LookupError:
            raise ParseError('return-semantic (if specified) must be one of the following: {0}'.format(', '.join(mapping.keys())))
    return rs

class tag_GetSet(tag):
    def __init__(self,args):
        self.r = GetSetDef(args["func"],get_ret_semantic(args))

class tag_Def(tag):
    def __init__(self,args):
        func = args['func']
        self.r = DefDef(get_valid_py_ident(args.get("name"),func))
        func = args.get('func')
        if func:
            # Don't parse the overload. gccxml will do that for us.
            self.r.overloads.append(Overload(tag_Def.operator_parse(func),get_ret_semantic(args),args.get("overload")))


    op_parse_re = re.compile('operator\b')

    @staticmethod
    def operator_parse(x):
        """Normalize operator function names"""
        m = tag_Def.op_parse_re.match(x)
        if m:
            return 'operator ' + ''.join(x[m.end():].split())
        return x

tagdefs = {
    "class" : tag_Class,
    "init" : tag_Init,
    "module" : tag_Module,
    "property" : tag_Property,
    "doc" : tag_Doc,
    "member" : tag_Member,
    'get' : tag_GetSet,
    'set' : tag_GetSet,
    'def' : tag_Def
}

def getspec(path):
    return parse(path,tagdefs)
