
from future_builtins import filter, map, zip


import re
import itertools
import os.path
import copy
import sys
import textwrap
import functools
import operator

from xmlparse import *
import err
import gccxml
import espectmpl as tmpl


TEST_NS = "___gccxml_types_test_ns___"
UNINITIALIZED_ERR_TYPE = "PyExc_RuntimeError"
TO_PY_FUNC = '__py_to_pyobject__'
FROM_PY_FUNC = '__py_from_pyobject__'
TRAVERSE_FUNC = '__py_traverse__'
CLEAR_FUNC = '__py_clear__'
CAST_AS_MEMBER_FIELD = '__py_cast_as_member_t__'

RET_MANAGED_REF = 1
RET_MANAGED_PTR = 2
RET_UNMANAGED_REF = 3
RET_COPY = 1001
RET_SELF = 1002

tmpl.env.globals['MANAGED_REF'] = RET_MANAGED_REF
tmpl.env.globals['MANAGED_PTR'] = RET_MANAGED_PTR
tmpl.env.globals['UNMANAGED_REF'] = RET_UNMANAGED_REF

GETTER = 1
SETTER = 2


SF_NO_ARGS = 1 # (PyObject *self)
SF_ONE_ARG = 2 # (PyObject *self, PyObject *o)
SF_TWO_ARGS = 3  # (PyObject *self, PyObject *o1, PyObject *o2)
SF_KEYWORD_ARGS = 4 # (PyObject *self, PyObject *args, PyObject *kwds)
SF_COERCE_ARGS = 5 # (PyObject **p1, PyObject **p2)
SF_TYPE_KEYWORD_ARGS = 8 # (PyTypeObject *subtype, PyObject *args, PyObject *kwds)

SF_RET_OBJ = 0
SF_RET_INT = 1
SF_RET_LONG = 2
SF_RET_SSIZE = 3
SF_RET_INT_VOID = 4 # the return type is int, -1 for an exception and 0 otherwise
SF_RET_INT_BOOL = 5 # the return type is int, 1 for True, 0 for False, -1 for an exception


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


EXTRA_VARS_NONE = 0
EXTRA_VARS_IDICT = 1
EXTRA_VARS_WEAKLIST = 2
EXTRA_VARS_BOTH = 3

EXTRA_VARS_SUFFIXES = ['','_i','_w','_wi']


class SpecificationError(err.Error):
    def __str__(self):
        return 'Specification Error: ' + super(SpecificationError,self).__str__()




class Scope(object):
    """A work-around for the absence of the 'nonlocal' keyword in Python 2.X"""
    def __init__(self,**args):
        self.__dict__.update(args)


def getDestructor(self):
    for m in self.members:
        if isinstance(m,gccxml.CPPDestructor):
            return m
    return None
gccxml.CPPClass.getDestructor = getDestructor


def compatible_args(f,given):
    """Return a copy of f with the subset of arguments from 'needed', specified
    by 'given' or None if the arguments don't match."""
    if accepts_args(f,[a.type for a in given]): return None
    if len(f.args) > len(given):
        newargs = f.args[0:len(given)]
        rf = copy.copy(f)
        rf.args = newargs
        return rf
    return f

def accepts_args(f,args):
    return (len(f.args) >= len(args) and 
            mandatory_args(f) <= len(args) and
            all(a.type == b for a,b in zip(f.args,args)))


def always_true(x):
    return True

def simple_member_lookup(x,name,test = always_true):
    return (real_type(m) for m in x.members if getattr(m,"canon_name",None) == name and test(m))

def min_access(m,min_access):
    if getattr(m,'access',sys.maxint) >= min_access:
        return m

    m = copy.copy(m)
    m.access = min_access


class BaseCacheItem:
    def __init__(self,had_virtual,data):
        self.had_virtual = had_virtual
        self.data = data

class BaseMembers(object):
    """Call a function on all direct and inherited members of a class.

    This class will pass an instance of itself to 'generate'. generate is
    expected to return a list. generate can compute a value using c, members()
    and base_members(). members() will return a generator that emits all direct
    members of c. base_members() returns a generator that emits a concatinated
    sequence of the lists produced by calling generate on each base class.

    Each member will have the correct access specifier
    (public/protected/private) with regard to the access specifier of the
    inheritance. e.g. for 'class A : private B { ... };' every member of B will
    be private.

    Each unique class is only visited once. If a class occurs more than once in
    an inheritance hierarchy, it's previous computed (by 'generate') value is
    reused, unless the inheritance is virtual. Only the first occurance of a
    virtually inherited class yields a value.

    """
    def __init__(self,c,generate,access = gccxml.ACCESS_PUBLIC,cache = None):
        self.c = c
        self.generate = generate
        self.access = access
        self.cache = {} if cache is None else cache

    def members(self):
        return (min_access(real_type(m),self.access) for m in self.c.members)

    def base_members(self):
        for b in self.c.bases:
            ci = self.cache.get(b.type)
            if ci is None:
                ci = BaseCacheItem(
                    b.virtual,
                    self.generate(
                        BaseMembers(
                            b.type,
                            self.generate,
                            max(self.access,b.access),
                            self.cache)))

                self.cache[b.type] = ci
            elif b.virtual:
                if ci.had_virtual:
                    # the members of virtual base classes are only counted once
                    continue

                ci.had_virtual = True

            yield b,ci.data

    def just_base_members(self):
        return reduce(operator.concat,(data for b,data in self.base_members()),[])

    def __call__(self):
        return self.generate(self)


def inherited_member_lookup(c,name,test = always_true,access = gccxml.ACCESS_PUBLIC):
    """Look up a class member name the same way C++ does"""

    def generate(bm):
        return list(m for m in bm.members() if getattr(m,"canon_name",None) == name and test(m)) or \
            bm.just_base_members()

    return BaseMembers(c,generate,access)()


def real_type(x):
    return real_type(x.type) if isinstance(x,gccxml.CPPTypeDef) else x

def _namespace_find(self,x,test):
    parts = x.split('::',1)
    matches = list(self.lookup(parts[0],test))
    if matches:
        if len(parts) == 2:
            if not isinstance(matches[0],(gccxml.CPPClass,gccxml.CPPNamespace)):
                raise SpecificationError('"{0}" is not a namespace, struct, class or union'.format(parts[0]))

            assert len(matches) == 1
            return _namespace_find(matches[0],parts[1],test)

        return matches
    return []

def raise_not_found_error(x):
    raise SpecificationError('could not find "{0}"'.format(x))

def namespace_find(self,x,test = always_true):
    """Find symbol x in this object's scope"""
    if x.startswith('::'): # explicit global namespace
        while self.context: self = self.context
        r = _namespace_find(self,x[2:],test)
        if r: return r
    else:
        # if the symbol isn't found in this scope, check the outer scope
        while self:
            r = _namespace_find(self,x,test)
            if r: return r
            self = self.context

    raise_not_found_error(x)

def class_find_member(self,x):
    """Find member x of this class."""
    parts = x.rsplit('::')

    if len(parts) == 2:
        context = self.find(parts[0])
        nonlocal = Scope(found=False)

        def generate(bm):
            if bm.c == context:
                nonlocal.found = True
                return inherited_member_lookup(bm.c,name=parts[1],access=bm.access)

            return bm.just_base_members()

        matches = BaseMembers(c,generate)()

        if not nonlocal.found:
            raise SpecificationError('"{0}" is not a base class of "{1}"'.format(parts[0],self.typestr()))

    else:
        matches = inherited_member_lookup(self,x)

    if matches: return matches
    raise_not_found_error(x)


gccxml.CPPClass.find = namespace_find
gccxml.CPPClass.lookup = inherited_member_lookup
gccxml.CPPClass.find_member = class_find_member

gccxml.CPPNamespace.find = namespace_find
gccxml.CPPNamespace.lookup = simple_member_lookup

gccxml.CPPUnion.find = namespace_find
gccxml.CPPUnion.lookup = simple_member_lookup


def get_unique_num():
    """Generates a unique number.

    This is used by ClassDef and others to generate unique C++ identifiers.

    """
    get_unique_num.nextnum += 1
    return get_unique_num.nextnum
get_unique_num.nextnum = 0


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


def mandatory_args(x):
    return len(list(itertools.takewhile(lambda a: a.default is None, x.args)))


def varargs(x):
    return x.args and x.args[-1] is gccxml.cppellipsis


def forwarding_args(args):
    return ','.join('{0} _{1}'.format(a.type.typestr(),i) for i,a in enumerate(args))

def forwarding_arg_vals(args):
    return ','.join('_{0}'.format(i) for i in xrange(len(args)))


def has_trivial_destructor(x):
    if not isinstance(x,gccxml.CPPClass): return True
    d = x.getDestructor()
    return (d is None or d.artificial) and \
        all(has_trivial_destructor(m) for m in x.members) and \
        all(has_trivial_destructor(b.type) for b in x.bases)


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

    def downcast_func(self):
        r = tmpl.typecheck_start.render(
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


class Overload:
    def __init__(self,func=None,retsemantic=None,args=None,static=False,arity=None,assign=False,bridge_virt=True):
        self.func = func
        self.retsemantic = retsemantic
        self.args = args
        self.static = static
        self.arity = arity
        self.assign = assign
        self.bridge_virt = bridge_virt
        self.uniquenum = get_unique_num()

    def gccxml_input(self,outfile):
        if self.args:
            # declare a dummy function with the arguments we want gccxml to
            # parse for us
            print >> outfile, 'void dummy_func_{0}({1});\n'.format(self.uniquenum,self.args)

    def get_parsed_args(self,tns):
        f = tns.find('dummy_func_{0}'.format(self.uniquenum))[0]
        assert isinstance(f,gccxml.CPPFunction)
        return f.args

class DefDef:
    def __init__(self,name = None,doc = None):
        self.name = name
        self.doc = doc
        self.overloads = []

    def gccxml_input(self,outfile):
        for o in self.overloads:
            o.gccxml_input(outfile)


class CallCode(object):
    """C++ code representing a function call with optional predefined argument values."""
    def __init__(self,code,binds=None,divided=False):
        self.code = code
        self.binds = binds or []
        self.divided = divided

    def output(self,args,ind):
        args = list(args)
        for i,val in self.binds:
            args.insert(i,val)

        joinargs = lambda _args: ','.join('\n'+ind+a for a in _args)
        if self.divided:
            return self.code.format(joinargs(args[0:-1]),args[-1])
        return self.code.format(joinargs(args))

class PureVirtualCallCode(object):
    def __init__(self,errval):
        self.errval = errval

    def output(self,args,ind):
        return '{0}PyErr_SetString(PyExc_NotImplementedError,not_implemented_msg);\n{0}return {1};'.format(ind,self.errval)

class BindableArg:
    def __init__(self,arg,val=None):
        self.arg = arg
        self.val = val

def is_const(x):
    return isinstance(x,gccxml.CPPCvQualifiedType) and x.const

class TypedOverload:
    def __init__(self,func,overload=None):
        self.func = func
        self.retsemantic = overload and overload.retsemantic
        self.argbinds = [BindableArg(a) for a in func.args]
        self.explicit_static = overload.static if overload else False
        self.bridge_virt = overload.bridge_virt if overload else False
        self._returns = None

        self.assign = overload and overload.assign
        if self.assign:
            ret = func.returns
            if not isinstance(ret,(gccxml.CPPReferenceType,gccxml.CPPPointerType)):
                raise SpecificationError('"{0}" must return a reference or pointer type to be assigned to'.format(func.canon_name))

            if is_const(ret.type):
                raise SpecificationError('The return value of "{0}" cannot be assigned to because it is const'.format(func.canon_name))

            self.argbinds.append(BindableArg(gccxml.CPPArgument(ret.type)))

            self._returns = gccxml.CPPReferenceType(ret.type)
            if hasattr(ret,'find'):
                try:
                    ops = ret.find('operator =')
                except SpecificationError:
                    # if operator= is not found, no action is required
                    pass
                else:
                    self._returns = ops[0].returns

    def bind(self,index,val):
        [a for a in self.argbinds if a.val is None][index].val = val

    @property
    def args(self):
        return [a.arg for a in self.argbinds if a.val is None]

    @property
    def returns(self):
        return self._returns or self.func.returns

    def can_accept(self,args):
        if not (mandatory_args(self) <= args <= len(self.args)):
            raise SpecificationError(
                '"{0}" must take {1} argument(s)'.format(
                    self.func.canon_name,
                    args + sum(1 for a in self.argbinds if a.val is not None)))

    @property
    def static(self):
        return self.explicit_static or (isinstance(self.func,gccxml.CPPMethod) and self.func.static)


def choose_overload(ov,options,tns):
    if ov.args is None:
        return [TypedOverload(f,ov) for f in options]

    args = ov.get_parsed_args(tns) if ov.args else []
    for f in options:
        newf = compatible_args(f,args)
        if newf:
            return [TypedOverload(newf,ov)]
            break

    raise SpecificationError(
        'No overload matches the given arguments. The candidates are:' +
        ''.join('\n({0})'.format(','.join(map(str,f.args))) for f in options))


def append_except(f):
    @functools.wraps(f)
    def wrapper(self,*args,**kwds):
        try:
            return f(self,*args,**kwds)
        except err.Error as e:
            e.info[self.what] = self.name
            raise
    return wrapper

class TypedDefDef(object):
    what = 'function'

    @append_except
    def __init__(self,scope,defdef,tns):
        self.name = defdef.name
        self.doc = defdef.doc
        self.overloads = []

        for ov in defdef.overloads:
            extratest = always_true
            if ov.arity is not None:
                extratest = lambda x: mandatory_args(x) <= ov.arity <= len(x.args)
            cf = scope.find(ov.func,extratest)

            # if the first one is a function, they all should be functions
            if not isinstance(cf[0],(gccxml.CPPFunction,gccxml.CPPMethod)):
                raise SpecificationError('"{0}" is not a function or method'.format(cf[0]))
            assert all(isinstance(f,(gccxml.CPPFunction,gccxml.CPPMethod)) for f in cf)

            # if there is more than one result, remove the const methods
            if len(cf) > 1:
                newcf = [f for f in cf if not (hasattr(f,'const') and f.const)]
                if newcf: cf = newcf

            self.overloads.extend(choose_overload(ov,cf,tns))


    def call_code_base(self,ov):
        return ov.func.canon_name

    def topy(self,conv,t,retsemantic):
        return conv.topy(t,retsemantic)

    def call_code_mid(self,conv,ov):
        code = self.call_code_base(ov) + '({0})'
        if ov.assign:
            if isinstance(ov.func.returns,gccxml.CPPReferenceType):
                code += ' = {1}'
            else:
                assert isinstance(ov.func.returns,gccxml.CPPPointerType)
                code = '*({0}) = {{1}}'.format(code)

        return CallCode(
            code,
            [(i,argbind.val) for i,argbind in enumerate(ov.argbinds) if argbind.val],
            ov.assign)

    def call_code(self,conv,ov):
        cc = self.call_code_mid(conv,ov)
        cc.code = (cc.code + '; Py_RETURN_NONE;' if ov.returns == conv.void else
            'return {0};'.format(self.topy(conv,ov.returns,ov.retsemantic).format(cc.code)))
        return cc

    def make_argss(self,conv):
        return [(self.call_code(conv,ov),ov.args) for ov in self.overloads]

    def check_args_ret(self,conv):
        # this gets overridden by SpecialMethod
        pass

    def function_call_var_args(self,conv,use_kwds,errval='0'):
        self.check_args_ret(conv)
        return conv.function_call(self.make_argss(conv),errval,use_kwds)

    def function_call_1arg(self,conv,ind=Tab(2),var='arg',errval='0'):
        self.check_args_ret(conv)
        return conv.function_call_narg(self.make_argss(conv),[var],errval,ind)

    def function_call_narg_fallthrough(self,conv,vars,ind=Tab(2)):
        self.check_args_ret(conv)
        return conv.function_call_narg_fallthrough(self.make_argss(conv),vars,ind)

    def function_call_narg(self,conv,vars,ind=Tab(2),errval='0'):
        self.check_args_ret(conv)
        return conv.function_call_narg(self.make_argss(conv),vars,errval,ind)

    def function_call_1arg_fallthrough(self,conv,ind=Tab(2),var='arg'):
        self.check_args_ret(conv)
        return conv.function_call_narg_fallthrough(self.make_argss(conv),[var],ind)

    def function_call_0arg(self,conv,ind=Tab(2)):
        self.check_args_ret(conv)
        assert len(self.overloads) == 1
        return ind.line(self.call_code(conv,self.overloads[0]).output([],ind))

    @append_except
    def _output(self,conv,prolog,type_extra,selfvar,funcnameprefix):
        arglens = [len(ov.args) for ov in self.overloads]
        maxargs = max(len(ov.args) for ov in self.overloads)
        minargs = min(mandatory_args(ov.func) for ov in self.overloads)

        if maxargs == 0:
            assert len(self.overloads) == 1
            type = 'METH_NOARGS'
            funcargs = ',PyObject *'
            code = self.function_call_0arg(conv)
        elif maxargs == 1 and minargs == 1 and not self.overloads[0].args[0].name:
            type = 'METH_O'
            funcargs = ',PyObject *arg'
            code = self.function_call_1arg(conv)
        elif len(self.overloads) == 1 and any(a.name for a in self.overloads[0].args): # is there a named argument?
            type = 'METH_VARARGS|METH_KEYWORDS'
            funcargs = ',PyObject *args,PyObject *kwds'
            code = self.function_call_var_args(conv,True)
        else:
            type = 'METH_VARARGS'
            funcargs = ',PyObject *args'
            code = self.function_call_var_args(conv,False)


        funcbody = tmpl.function.format(
            rettype = 'PyObject *',
            epilog = '',
            name = funcnameprefix + self.name,
            args = selfvar + funcargs,
            code = prolog + code,
            errval = '0')

        tableentry = '{{"{name}",reinterpret_cast<PyCFunction>({funcnameprefix}{name}),{type}{typeextra},{doc}}}'.format(
            funcnameprefix = funcnameprefix,
            name = self.name,
            type = type,
            typeextra = type_extra,
            doc = tmpl.quote_c(self.doc) if self.doc else '0')

        return tableentry,funcbody

    def output(self,conv):
        return self._output(conv,'','','PyObject*','func_')


def base_prefix(x):
    if x.static:
        return x.full_name

    return 'base.' + x.canon_name

def pure_virtual(x):
    return isinstance(x,gccxml.CPPMethod) and x.pure_virtual

def is_virtual(x):
    return isinstance(x,gccxml.CPPMethod) and x.virtual

def is_static(x):
    return isinstance(x,gccxml.CPPMethod) and x.static

class TypedMethodDef(TypedDefDef):
    what = 'method'
    selfvar = 'reinterpret_cast<PyObject*>(self)'

    def __init__(self,classdef,defdef,tns):
        super(TypedMethodDef,self).__init__(classdef.type,defdef,tns)
        self.classdef = classdef

        for ov in self.overloads:
            if isinstance(ov.func,gccxml.CPPFunction) and not ov.explicit_static:
                if len(ov.func.args) == 0 or strip_refptr(ov.func.args[0].type) != self.classdef.type:
                    self.odd_function(ov)
                else:
                    ov.bind(0,'&base' if isinstance(ov.func.args[0].type,gccxml.CPPPointerType) else 'base')

    def static(self):
        return all(ov.static for ov in self.overloads)

    def call_code_base(self,ov):
        if isinstance(ov.func,gccxml.CPPMethod):
            if ov.func.static:
                return ov.func.full_name

            f = ov.func.canon_name
            if is_virtual(ov.func):
                # don't bother calling the overridden method from X_virt_handler
                f = self.classdef.type.typestr() + '::' + f
            return 'base.' + f

        return super(TypedMethodDef,self).call_code_base(ov)

    def call_code(self,conv,ov):
        if pure_virtual(ov.func):
            return PureVirtualCallCode('0')

        if ov.retsemantic == RET_SELF:
            cc = self.call_code_mid(conv,ov)
            cc.code += '; Py_INCREF({0}); return {0};'.format(self.selfvar)
            return cc
        return super(TypedMethodDef,self).call_code(conv,ov)

    def odd_function(self,ov):
        raise SpecificationError('The first parameter of "{0}" should be of type "{1}" or be a reference or pointer to it.'.format(ov.func.name,self.classdef.type.typestr()))

    def topy(self,conv,t,retsemantic):
        return conv.topy(t,retsemantic,'self')

    def all_pure_virtual(self):
        return all(pure_virtual(ov.func) for ov in self.overloads)

    def function_call_var_args(self,conv,use_kwds,errval='0'):
        if self.all_pure_virtual():
            return PureVirtualCallCode(errval).output(None,Tab(2))

        return super(TypedMethodDef,self).function_call_var_args(conv,use_kwds,errval)

    def function_call_1arg(self,conv,ind=Tab(2),var='arg',errval='0'):
        if self.all_pure_virtual():
            return PureVirtualCallCode(errval).output(None,ind)

        return super(TypedMethodDef,self).function_call_1arg(conv,ind,var,errval)

    def function_call_narg(self,conv,vars,ind=Tab(2),errval='0'):
        if self.all_pure_virtual():
            return PureVirtualCallCode(errval).output(None,ind)

        return super(TypedMethodDef,self).function_call_narg(conv,vars,ind,errval)

    def function_call_0arg(self,conv,ind=Tab(2),errval='0'):
        if self.all_pure_virtual():
            return PureVirtualCallCode(errval).output(None,ind)

        return super(TypedMethodDef,self).function_call_0arg(conv,ind)

    def output(self,conv):
        prolog = ''
        type_extra = ''

        if self.static():
            type_extra = '|METH_STATIC'
        else:
            prolog = self.classdef.method_prolog()

        return self._output(
            conv,
            prolog,
            type_extra,
            'obj_{0} *self'.format(self.classdef.name),
            'obj_{0}_method_'.format(self.classdef.name))


class SpecialMethod(TypedMethodDef):
    def __init__(self,classdef,defdef,tns,argtype,rettype = SF_RET_OBJ,defretsemantic = None):
        super(SpecialMethod,self).__init__(classdef,defdef,tns)
        self.argtype = argtype
        self.rettype = rettype
        if defretsemantic:
            for ov in self.overloads:
                if not ov.retsemantic:
                    ov.retsemantic = defretsemantic

    @staticmethod
    def check_static(ov):
        if not ov.func.static:
            raise SpecificationError('"{0}" must be static'.format(ov.func.canon_name))

    def check_args_ret(self,conv):
        for ov in self.overloads:
            if self.argtype <= SF_TWO_ARGS:
                ov.can_accept(self.argtype - 1)
            elif self.argtype == SF_COERCE_ARGS:
                # no conversion is done
                ov.can_accept(2)
                t = cptr(cptr(gccxml.CPPBasicType('PyObject')))
                if ov.args[0].type != t or ov.args[1].type != t:
                    raise SpecificationError('"{0}" must accept 2 arguments of PyObject**'.format(ov.func.canon_name))
                self.check_static(ov)
            elif self.argtype == SF_TYPE_KEYWORD_ARGS:
                # no conversion is done for the first arg
                if len(ov.args) == 0:
                    raise Specification('"{0}" must accept at least one argument'.format(ov.func.canon_name))
                if ov.args[0].type != cptr(gccxml.CPPBasicType('PyTypeObject')):
                    raise Specification('The first argument to "{0}" must be PyTypeObject*'.format(ov.func.canon_name))
                self.check_static(ov)

            if self.rettype in (SF_RET_INT,SF_RET_LONG,SF_RET_SSIZE):
                if not ov.returns in conv.integers:
                    if ov.assign:
                        raise SpecificationError('The expression "({0}() = x)" must yield an integer type'.format(ov.func.canon_name))
                    raise SpecificationError('"{0}" must return an integer type'.format(ov.func.canon_name))
            elif self.rettype == SF_RET_INT_BOOL:
                if not (ov.returns in conv.integers or ov.returns == conv.bool):
                    if ov.assign:
                        raise SpecificationError('The expression "({0}() = x)" must yield an integer or bool type'.format(ov.func.canon_name))
                    raise SpecificationError('"{0}" must return an integer or bool type'.format(ov.func.canon_name))

    def call_code_cast(self,conv,ov,t):
        cc = self.call_code_mid(conv,ov)
        cc.code = 'return static_cast<{0}>({1});'.format(t,cc.code)

    def call_code(self,conv,ov):
        if self.rettype == SF_RET_INT or self.rettype == SF_RET_INT_BOOL:
            return self.call_code_cast(conv,ov,'int')

        if self.rettype == SF_RET_LONG:
            return self.call_code_cast(conv,ov,'long')

        if self.rettype == SF_RET_SSIZE:
            return self.call_code_cast(conv,ov,'Py_ssize_t')

        if self.rettype == SF_RET_INT_VOID:
            cc = self.call_code_mid(conv,ov)
            cc.code += '; return 0;'
            return cc

        assert self.rettype == SF_RET_OBJ
        return super(SpecialMethod,self).call_code(conv,ov)

    @append_except
    def output(self,conv,ind=Tab(2)):
        errval = '-1'
        if self.rettype in (SF_RET_INT,SF_RET_INT_VOID,SF_RET_INT_BOOL):
            ret = 'int'
        elif self.rettype == SF_RET_LONG:
            ret = 'long'
        elif self.rettype == SF_RET_SSIZE:
            ret = 'Py_ssize_t'
        else:
            assert self.rettype == SF_RET_OBJ
            ret = 'PyObject *'
            errval = '0'

        if self.argtype == SF_NO_ARGS:
            argextra = ''
            code = self.function_call_0arg(conv,ind)
        elif self.argtype == SF_ONE_ARG:
            argextra = ',PyObject *arg'
            code = self.function_call_1arg(conv,ind,errval=errval)
        elif self.argtype == SF_TWO_ARGS:
            argextra = ',PyObject *arg1,PyObject *arg2'
            code = self.function_call_narg(conv,['arg1','arg2'],ind,errval)
        elif self.argtype == SF_KEYWORD_ARGS:
            argextra = ',PyObject *args,PyObject *kwds'
            code = self.function_call_var_args(conv,True,errval)
        else:
            # nothing else implemented
            assert False

        return tmpl.function.format(
            rettype = ret,
            name = 'obj_{0}_{1}'.format(self.classdef.name,self.name),
            args = 'obj_{0} *self{1}'.format(self.classdef.name,argextra),
            epilog = '',
            code = self.classdef.method_prolog() + code,
            errval = errval)


class FOpMethod(SpecialMethod):
    selfvar = 'a'


class BinaryROpMethod(SpecialMethod):
    selfvar = 'b'

    def __init__(self,classdef,defdef,tns):
        super(BinaryROpMethod,self).__init__(classdef,defdef,tns,SF_ONE_ARG)

    def odd_function(self,ov):
        if not(len(ov.func.args) >= 2 and strip_refptr(ov.func.args[1].type) == self.classdef.type):
            raise SpecificationError('The first or second parameter of "{0}" should be of type "{1}" or be a reference or pointer to it.'.format(ov.func.name,self.classdef.type.typestr()))

        ov.bind(1,'&base' if isinstance(ov.func.args[1].type,gccxml.CPPPointerType) else 'base')


class TypedInitDef:
    def __init__(self,scope,idef,tns):
        self.doc = None
        self.overloads = []

        cons = [con for con in scope.members if isinstance(con,gccxml.CPPConstructor) and con.access == gccxml.ACCESS_PUBLIC]
        if idef:
            self.doc = idef.doc
            for ov in idef.overloads:
                self.overloads.extend(choose_overload(ov,cons,tns))
        else:
            realconstructs = [c for c in cons if not c.artificial]
            if len(realconstructs) == 1:
                self.overloads = [TypedOverload(realconstructs[0])]
            else:
                # if there is more than one constructor and an overload wasn't specified, look for one with no arguments (the default constructor)
                for c in cons:
                    if not c.args:
                        self.overloads = [TypedOverload(c)]
                        break
                else:
                    raise SpecificationError('There is more than one constructor and there is no default. An overload must be specified in the spec file.')

    def output(self,conv,typestr):
        cc = CallCode('new(addr) {0}({{0}}); return 0;'.format(typestr))
        return conv.function_call([(cc,ov.args) for ov in self.overloads],'-1',True)

class PropertyDef:
    def __init__(self,name,get=None,set=None):
        self.name = name
        self.get = get
        self.set = set
        self.doc = None

class TypedPropertyDef:
    def __init__(self,classdef,propdef,tns):
        self.name = propdef.name
        self.doc = propdef.doc
        self.get = propdef.get and SpecialMethod(classdef,propdef.get,tns,SF_NO_ARGS,SF_RET_OBJ)
        self.set = propdef.set and SpecialMethod(classdef,propdef.set,tns,SF_ONE_ARG,SF_RET_INT_VOID)

    def output(self,conv):
        r = ''
        if self.get:
            code = self.get.function_call_0arg(conv)
            if not self.get.static(): code = self.get.classdef.method_prolog() + code
            r = tmpl.property_get.render(
                cname = self.get.classdef.name,
                name = self.name,
                checkinit = True,
                code = code)

        if self.set:
            code = self.set.function_call_1arg(conv)
            if not self.set.static(): code = self.set.classdef.method_prolog() + code
            r += tmpl.property_set.render(
                cname = self.set.classdef.name,
                name = self.name,
                checkinit = True,
                code = code)

        return r

    def table_entry(self):
        return tmpl.property_table.render(
            name = self.name,
            cname = self.get.classdef.name,
            get = bool(self.get),
            set = bool(self.set),
            doc = self.doc)


class MemberDef:
    doc = None


def member_type(x):
    if not isinstance(x,gccxml.CPPField):
        raise SpecificationError('"{0}" is not a member variable'.format(x.full_name))
    return x.type

class AttrAccess(object):
    def __init__(self,classdef,seq):
        self.seq = seq
        member = classdef.type.find(seq[0])[0]
        self.type = member_type(member)
        self.static = member.static

        for m in seq[1:]:
            if isinstance(m,basestring):
                if not isinstance(self.type,(gccxml.CPPClass,gccxml.CPPUnion)):
                    raise SpecificationError('"{0}" is not a class, struct or union'.format(self.type.full_name))
                self.type = member_type(self.type.find(m)[0])
            else:
                # m should be number
                if not isinstance(self.type,(gccxml.CPPArrayType,gccxml.CPPPointerType)):
                    raise SpecificationError('"{0}" is not an array or pointer type'.format(self.type.full_name))
                self.type = self.type.type

    @property
    def canon_name(self):
        return self.seq[0] + ''.join('.'+s if isinstance(s,basestring) else '[{0}]'.format(s) for s in self.seq[1:])

class TypedMemberDef:
    what = 'attr'

    def __init__(self,classdef,memdef):
        self.classdef = classdef
        self.name = memdef.name
        self.doc = memdef.doc
        self.readonly = memdef.readonly
        self.cmember = AttrAccess(classdef,memdef.cmember)

    def getter_type(self,conv):
        return self.cmember.type if conv.member_macro(self.cmember.type) else gccxml.CPPReferenceType(self.cmember.type)

    @append_except
    def output(self,conv):
        r = ''
        if self.really_a_property(conv):
            code = '        return {0};'.format(
                conv.topy(self.getter_type(conv),RET_MANAGED_REF,'self').format(base_prefix(self.cmember)))
            if not self.cmember.static: code = self.classdef.method_prolog() + code
            r = tmpl.property_get.render(
                cname = self.classdef.name,
                name = self.name,
                checkinit = True,
                code = code)

            if not self.readonly:
                code = '        {0} = {1};\n        return 0;'.format(base_prefix(self.cmember),conv.frompy(self.cmember.type)[0].format('arg'))
                if not self.cmember.static: code = self.classdef.method_prolog() + code
                r += tmpl.property_set.render(
                    cname = self.classdef.name,
                    name = self.name,
                    checkinit = True,
                    code = code)
        return r

    def really_a_property(self,conv):
        return self.classdef.variable_storage() or not conv.member_macro(self.cmember.type)

    def table_entry(self,conv):
        mm = conv.member_macro(self.cmember.type)
        if self.classdef.variable_storage() or not mm:
            r = tmpl.property_table.render(
                name = self.name,
                cname = self.classdef.name,
                get = True,
                set = not self.readonly,
                doc = self.doc)
            return True,r

        r = '{{const_cast<char*>("{name}"),{type},offsetof(obj_{classdefname},base) + offsetof({classname},{mname}),{flags},{doc}}}'.format(
            name = self.name,
            type = mm,
            classdefname = self.classdef.name,
            classname = self.classdef.type.typestr(),
            mname = self.cmember.canon_name,
            flags = 'READONLY' if self.readonly else '0',
            doc = 'const_cast<char*>({0})'.format(tmpl.quote_c(self.doc)) if self.doc else '0')
        return False,r


class GetSetDef:
    def __init__(self,func,retsemantic = None):
        self.func = func
        self.retsemantic = retsemantic



class MethodDict(object):
    aliases = {
        '<' : '__lt__',
        '<=' : '__le__',
        '==' : '__eq__',
        '!=' : '__ne__',
        '>' : '__gt__',
        '>=' : '__ge__',
        '()' : '__call__',
        '+' : '__add__',
        '+=' : '__iadd__',
        '-' : '__sub__',
        '-=' : '__isub__',
        '*' : '__mul__',
        '*=' : '__imul__',
        '**' : '__pow__',
        '**=' : '__ipow__',
        '/' : '__div__',
        '/=' : '__idiv__',
        '//' : '__floordiv__',
        '//=' : '__ifloordiv__',
        '<<' : '__lshift__',
        '<<=' : '__ilshift__',
        '>>' : '__rshift__',
        '>>=' : '__irshift__',
        '&' : '__and__',
        '&=' : '__iand__',
        '^' : '__xor__',
        '^' : '__ixor__',
        '|' : '__or__',
        '|=' : '__ior__',
        '~' : '__invert__'}

    def __init__(self):
        self.data = {}

    def __getitem__(self,key):
        return self.data[MethodDict.aliases.get(key,key)]

    def __setitem__(self,key,value):
        self.data[MethodDict.aliases.get(key,key)] = value

    def get(self,key,default=None):
        return self.data.get(MethodDict.aliases.get(key,key),default)

    def itervalues(self):
        return self.data.itervalues()


class _NoInit:
    """A special value meaning no __init__ method is to be created"""
    def __nonzero__(self):
        return False

NoInit = _NoInit()

class QualifiedField(object):
    def __init__(self,name,type,offset):
        self.name = name
        self.type = type
        self.offset = offset

class ClassDef:
    def __init__(self,name,type,new_initializes=False,instance_dict=True,weakref=True,use_gc=True,gc_include=None,gc_ignore=None):
        self.name = name
        self.type = type
        self.new_initializes = new_initializes
        self.constructor = None
        self.methods = MethodDict()
        self.properties = []
        self.vars = []
        self.doc = None
        self.instance_dict = instance_dict
        self.weakref = weakref
        self.use_gc = use_gc
        self.gc_include = gc_include
        self.gc_ignore = gc_ignore
        self.uniquenum = get_unique_num()

    @property
    def template(self):
        return '<' in self.type

    def gccxml_input(self,outfile):
        # create a typedef so we don't have to worry about default arguments
        # and arguments specified by typedef in templates
        print >> outfile, 'typedef {0} class_type_{1};\n'.format(self.type,self.uniquenum)

        fields = []
        if self.gc_include: fields.extend(self.gc_include)
        if self.gc_ignore: fields.extend(self.gc_ignore)
        for i,f in enumerate(fields):
            print >> outfile, field_offset_and_type.format(self.uniquenum,i,f)

        for m in self.methods.itervalues():
            m.gccxml_input(outfile)

        if self.constructor:
            self.constructor.gccxml_input(outfile)

    def _get_field(self,tns,i):
        o = tns.find('class_{0}_field_offset_{1}'.format(self.uniquenum,i))[0]
        assert isinstance(o,gccxml.CPPVariable) and o.init.isdigit()
        o = int(o.init)

        t = tns.find('class_{0}_field_type_{1}'.format(self.uniquenum,i))[0]
        return QualifiedField(f,t,o)

    def get_types(self,tns):
        ct = tns.find('class_type_{0}'.format(self.uniquenum))[0]

        i = 0
        include = []
        ignore = []
        if self.gc_include:
            for f in self.gc_include:
                include.append(self._get_field(i))
                i += 1

        if self.gc_ignore:
            for f in self.gc_ignore:
                ignore.append(self._get_field(i))
                i += 1

        return ct,include,ignore


def splitdefdef23code(defdef,conv,vars,ind=Tab(2)):
    a = copy.copy(defdef)
    b = copy.copy(defdef)
    a.argtype = SF_ONE_ARG
    b.argtype = SF_TWO_ARGS
    ovs = a.overloads
    a.overloads = []
    b.overloads = []
    for ov in ovs:
        inserted = False
        if mandatory_args(ov) <= 1 <= len(ov.args):
            inserted = True
            a.overloads.append(ov)
        if mandatory_args(ov) <= 2 <= len(ov.args):
            inserted = True
            b.overloads.append(ov)

        if not inserted:
            raise SpecificationError('"{0}" must take 1 or 2 arguments'.format(ov.func.canon_name))

    if not a.overloads: a = None
    if not b.overloads: b = None
    r = ind.line('if({0} == Py_None) {{'.format(vars[1]))
    if a.overloads: r += a.function_call_1arg_fallthrough(conv,ind+1,var=vars[0])
    r += ind.line('} else {')
    if b.overloads: r += b.function_call_narg_fallthrough(conv,vars,ind+1)
    r += ind.line('}')

    return r


def bindpyssize(conv,f,arg):
    for ov in f.overloads:
        try:
            ov.bind(0,conv.from_py_ssize_t[ov.args[0].type].format(arg))
        except KeyError:
            raise SpecificationError('"{0}" must accept an integer type as its first argument'.format(ov.func.name))


def default_to_ov(args):
    """turn default values into overloads"""
    newargs = []
    for a in args:
        if a.default:
            yield newargs[:]
            a = copy.copy(a)
            a.default = None
        newargs.append(a)
    yield newargs


def subname(a,b):
    return '{0}::{1}'.format(a,b)

GC_IGNORE = 1
GC_INCLUDE = 2
GC_FUNCTION = 3 # means the field is covered by <gc-include>

def recursive_qf_fields(fields):
    for f in fields:
        yield f
        for sub_f in recursive_qf_fields(f.components):
            yield sub_f

class QualifiedFieldHandling(object):
    def __init__(self,name,type,access,offset,base_handler=None,handle_gc=None):
        self.name = name
        self.type = type
        self.access = access
        self.offset = offset
        self.handle_gc = None
        self.base_handler = None
        self.components = []

    def derived(self,name,offset,base_handler=None):
        return QualifiedFieldHandling(subname(name,self.name),self.type,self.access,self.offset+offset,base_handler)

    @property
    def end_offset(self):
        return self.offset + self.type.size

    @staticmethod
    def from_field(field,handle_gc=None):
        return QualifiedFieldHandling(field.name,field.type,None,field.offset,None,handle_gc)

class TypeMismatch(err.Error):
    pass

class RedundantSpecification(err.Error):
    pass

def check_base_handler(fields,f,handle):
    """Check if the base handler is still usable and remove it if not.

    If a field is ignored that was included in an exposed base class, the base
    class' GC handler functions cannot be re-used in this class.

    """
    b_handler = f.base_handler

    if b_handler:
        if f.handle_gc == GC_FUNCTION:
            if handle == GC_IGNORE:
                emit_warning(SpecificationError(
                    ('"{0}" is a member of a base class that uses '+
                    '<gc-handler>. Ignoring it here has no effect.')
                    .format(f.name)))
            elif handle == GC_INCLUDE:
                emit_warning(SpecificationError(
                    ('"{0}" is a member of a base class that uses '+
                    '<gc-handler>. If the member is handled by the base class,'+
                    'including it here will cause it to be handled twice.')
                    .format(f.name)))
        elif handle == GC_IGNORE and f.handle_gc == GC_INCLUDE:
            for field in recursive_qf_fields(fields):
                if field.base_handler == handler:
                    field.base_handler = None

def qf_handle(fields,target,handle):
    assert handle

    for i,f in enumerate(fields):
        if target.offset < f.offset:
            raise TypeMismatch('"{0}" does not correspond to a defined field'.format(target.name))

        if f.offset == target.offset:
            if target.type.size < f.type.size:
                qf_handle(f.components,target,handle)
                break

            if i+1 == len(fields) or target.end_offset <= fields[i+1].offset:
                if f.type != target.type:
                    # TODO: don't emit this warning for proper union members
                    emit_warning(TypeMismatch('Field "{0}" is of type "{1}" but is cast as "{2}"'.format(f.name,f.typestr(),target.type.typestr())))
                check_base_handler(fields,f,handle)
                if f.handle_gc == handle:
                    emit_warning(RedundantSpecification(('Field "{0}" was already included' if handle else 'Field "{0}" was already ignored').format(f.name)))
                f.handle_gc = handle
                break

            raise TypeMismatch('"{0}" overlaps two or more defined fields'.format(target.name))
        
        if target.end_offset <= f.end_offset:
            qf_handle(f.components,target,handle)
            break
    else:
        # this might mean we are dealing with a variable-sized type
        emit_warning(TypeMismatch('"{0}" is farther than any defined field'.format(target.name)))
        fields.append(QualifiedFieldHandling.from_field(target))


def qualified_fields(c,classdefs):
    def generate(bm):
        fields = list(QualifiedFieldHandling(m.canon_name,m.type,m.access,m.offset) for m in bm.members() if isinstance(m,gccxml.CPPField) and not m.static)
        for b,subfields in bm.base_members():
            typed = classdefs.get(b.type)
            if typed:
                typed = typed[0]
                subfields = typed.gc_fields
            
            fields.extend(m.derived(b.type.name,b.offset,typed) for m in subfields)
        return sorted(fields,key=(lambda x: x.offset))

    return BaseMembers(c,generate)()


def find_field(c,field):
    m = c.find_member(field)
    if not isinstance(m[0],gccxml.CPPField):
        raise SpecificationError('"{0}" is not a member variable'.format(field))

    if len(m) > 1:
        raise SpecificationError('"{0}" is ambiguous in this context')


class TypedClassDef:
    what = 'class'

    @append_except
    def __init__(self,scope,classdef,tns):
        self.name = classdef.name
        self.uniquenum = classdef.uniquenum
        self.type,self.gc_include,self.gc_ignore = classdef.get_types(tns)
        self.new_initializes = classdef.new_initializes
        self._instance_dict = classdef.instance_dict
        self._weakref = classdef.weakref
        self._use_gc = classdef.use_gc
        self.no_destruct = has_trivial_destructor(self.type)

        if not isinstance(self.type,gccxml.CPPClass):
            raise SpecificationError('"{0}" is not a class or struct'.format(classdef.type))

        self.constructor = None if classdef.constructor is NoInit else TypedInitDef(self.type,classdef.constructor,tns)

        # TODO: allow this by putting the function call inside ob_<name>_dealloc
        if '__del__' in classdef.methods.data:
            raise SpecificationError('__del__ cannot be defined using <def>. Put the code in the destructor instead.')

        if '__init__' in classdef.methods.data:
            raise SpecificationError('__init__ cannot be defined using <def>. Use <init>.')

        BinaryIOpMethod = functools.partial(SpecialMethod,argtype=SF_ONE_ARG,defretsemantic=RET_SELF)
        TernaryIOpMethod = functools.partial(SpecialMethod,argtype=SF_TWO_ARGS,defretsemantic=RET_SELF)
        BinaryFOpMethod = functools.partial(FOpMethod,argtype=SF_ONE_ARG)
        TernaryFOpMethod = functools.partial(FOpMethod,argtype=SF_TWO_ARGS)
        NoArgs = functools.partial(SpecialMethod,argtype=SF_NO_ARGS)
        OneArg = functools.partial(SpecialMethod,argtype=SF_ONE_ARG)
        TwoArgs = functools.partial(SpecialMethod,argtype=SF_TWO_ARGS)
        KeywordArgs = functools.partial(SpecialMethod,argtype=SF_KEYWORD_ARGS)
        NoArgsInt = functools.partial(SpecialMethod,argtype=SF_NO_ARGS,rettype=SF_RET_INT)
        NoArgsIntBool = functools.partial(SpecialMethod,argtype=SF_NO_ARGS,rettype=SF_RET_INT_BOOL)
        OneArgInt = functools.partial(SpecialMethod,argtype=SF_ONE_ARG,rettype=SF_RET_INT)
        OneArgIntBool = functools.partial(SpecialMethod,argtype=SF_ONE_ARG,rettype=SF_RET_INT_BOOL)
        NoArgsLong = functools.partial(SpecialMethod,argtype=SF_NO_ARGS,rettype=SF_RET_LONG)
        TwoArgsInt = functools.partial(SpecialMethod,argtype=SF_TWO_ARGS,rettype=SF_RET_INT)
        CoerceArgsInt = functools.partial(SpecialMethod,argtype=SF_COERCE_ARGS,rettype=SF_RET_INT)
        NoArgsSSize = functools.partial(SpecialMethod,argtype=SF_NO_ARGS,rettype=SF_RET_SSIZE)

        TwoArgsVoid = functools.partial(SpecialMethod,argtype=SF_TWO_ARGS,rettype=SF_RET_INT_VOID)

        # TypedOverload.bind will be used to cover the Py_ssize argument
        SSizeArg = NoArgs
        SSizeObjArgsVoid = functools.partial(SpecialMethod,argtype=SF_ONE_ARG,rettype=SF_RET_INT_VOID)
        SSizeIOpMethod = functools.partial(SpecialMethod,argtype=SF_NO_ARGS,defretsemantic=RET_SELF)

        self.special_methods = {}
        for key,mtype in (
            ('__new__',          KeywordArgs), # tp_new
            ('__repr__',         NoArgs), # tp_repr
            ('__str__',          NoArgs), # tp_str
            ('__lt__',           OneArg), # tp_richcompare
            ('__le__',           OneArg), # tp_richcompare
            ('__eq__',           OneArg), # tp_richcompare
            ('__ne__',           OneArg), # tp_richcompare
            ('__gt__',           OneArg), # tp_richcompare
            ('__ge__',           OneArg), # tp_richcompare
            ('__cmp__',          OneArgInt), # tp_compare
            ('__hash__',         NoArgsLong), # tp_hash
            ('__nonzero__',      NoArgsIntBool), # tp_as_number.nb_nonzero
            ('__getattr__',      OneArg), # tp_getattro
            ('__setattr__',      TwoArgs), # tp_setattro
            ('__get__',          TwoArgs), # tp_descr_get
            ('__set__',          TwoArgsInt), # tp_descr_set
            ('__call__',         KeywordArgs), # tp_call
            ('__iter__',         NoArgs), # tp_iter
            ('next',             NoArgs), # tp_iternext
            ('__contains__',     OneArgIntBool), # tp_as_sequence.sq_contains
            ('__add__',          BinaryFOpMethod), # tp_as_number.nb_add
            ('__radd__',         BinaryROpMethod),
            ('__sub__',          BinaryFOpMethod), # tp_as_number.nb_subtract
            ('__rsub__',         BinaryROpMethod),
            ('__mul__',          BinaryFOpMethod), # tp_as_number.nb_multiply
            ('__rmul__',         BinaryROpMethod),
            ('__floordiv__',     BinaryFOpMethod), # tp_as_number.nb_floor_divide
            ('__rfloordiv__',    BinaryROpMethod),
            ('__mod__',          BinaryFOpMethod), # tp_as_number.nb_remainder
            ('__rmod__',         BinaryROpMethod),
            ('__divmod__',       BinaryFOpMethod), # tp_as_number.nb_divmod
            ('__rdivmod__',      BinaryROpMethod),
            ('__pow__',          TernaryFOpMethod), # tp_as_number.nb_power
            ('__rpow__',         BinaryROpMethod),
            ('__lshift__',       BinaryFOpMethod), # tp_as_number.nb_lshift
            ('__rlshift__',      BinaryROpMethod),
            ('__rshift__',       BinaryFOpMethod), # tp_as_number.nb_rshift
            ('__rrshift__',      BinaryROpMethod),
            ('__and__',          BinaryFOpMethod), # tp_as_number.nb_and
            ('__rand__',         BinaryROpMethod),
            ('__xor__',          BinaryFOpMethod), # tp_as_number.nb_xor
            ('__rxor__',         BinaryROpMethod),
            ('__or__',           BinaryFOpMethod), # tp_as_number.nb_or
            ('__ror__',          BinaryROpMethod),
            ('__div__',          BinaryFOpMethod), # tp_as_number.nb_divide
            ('__rdiv__',         BinaryROpMethod),
            ('__truediv__',      BinaryFOpMethod), # tp_as_number.nb_true_divide
            ('__rtruediv__',     BinaryROpMethod),
            ('__iadd__',         BinaryIOpMethod), # tp_as_number.nb_inplace_add
            ('__isub__',         BinaryIOpMethod), # tp_as_number.nb_inplace_subtract
            ('__imul__',         BinaryIOpMethod), # tp_as_number.nb_inplace_multiply
            ('__idiv__',         BinaryIOpMethod), # tp_as_number.nb_inplace_divide
            ('__itruediv__',     BinaryIOpMethod), # tp_as_number.nb_inplace_true_divide
            ('__ifloordiv__',    BinaryIOpMethod), # tp_as_number.nb_inplace_floor_divide
            ('__imod__',         BinaryIOpMethod), # tp_as_number.nb_inplace_remainder
            ('__ipow__',         TernaryIOpMethod), # tp_as_number.nb_inplace_power
            ('__ilshift__',      BinaryIOpMethod), # tp_as_number.nb_inplace_lshift
            ('__irshift__',      BinaryIOpMethod), # tp_as_number.nb_inplace_rshift
            ('__iand__',         BinaryIOpMethod), # tp_as_number.nb_inplace_and
            ('__ixor__',         BinaryIOpMethod), # tp_as_number.nb_inplace_xor
            ('__ior__',          BinaryIOpMethod), # tp_as_number.nb_inplace_or
            ('__neg__',          NoArgs), # tp_as_number.nb_negative
            ('__pos__',          NoArgs), # tp_as_number.nb_positive
            ('__abs__',          NoArgs), # tp_as_number.nb_absolute
            ('__invert__',       NoArgs), # tp_as_number.nb_invert
            ('__int__',          NoArgs), # tp_as_number.nb_int
            ('__long__',         NoArgs), # tp_as_number.nb_long
            ('__float__',        NoArgs), # tp_as_number.nb_float
            ('__oct__',          NoArgs), # tp_as_number.nb_oct
            ('__hex__',          NoArgs), # tp_as_number.nb_hex
            ('__index__',        NoArgs), # tp_as_number.nb_index
            ('__coerce__',       CoerceArgsInt), # tp_as_number.nb_coerce

            # made-up names for special functions that don't have a distinct equivalent in Python
            ('__concat__',       OneArg), # tp_as_sequence.sq_concat
            ('__iconcat__',      BinaryIOpMethod), # tp_as_sequence.sq_inplace_concat
            ('__repeat__',       SSizeArg), # tp_as_sequence.sq_repeat
            ('__irepeat__',      SSizeIOpMethod), # tp_as_sequence.sq_inplace_repeat
            ('__mapping__len__',   NoArgsSSize), # tp_as_mapping.mp_length
            ('__sequence__len__',  NoArgsSSize), # tp_as_sequence.sq_length
            ('__mapping__getitem__', OneArg), # tp_as_mapping.mp_subscript
            ('__sequence__getitem__', SSizeArg), # tp_as_sequence.sq_item
            ('__mapping__setitem__', TwoArgsVoid), # tp_as_mapping.mp_ass_subscript
            ('__sequence__setitem__', SSizeObjArgsVoid) # tp_as_sequence.sq_ass_item
        ):
            m = classdef.methods.data.pop(key,None)
            if m: self.special_methods[key] = mtype(self,m,tns)

        self.methods = [TypedMethodDef(self,dd,tns) for dd in classdef.methods.data.itervalues()]

        self.properties = [TypedPropertyDef(self,pd,tns) for pd in classdef.properties]
        self.vars = [TypedMemberDef(self,mdef) for mdef in classdef.vars]
        self.doc = classdef.doc

        self.bases = []
        self.derived = []
        self.features = set()
        self.needs_mode_var = False
        self.gc_fields = None # this is computed later

    def basecount(self):
        return sum(1 + b.basecount() for b in self.bases)

    @property
    def dynamic(self):
        return len(self.bases) > 1

    # a seperate property in case a dynamic declaration is ever needed for a
    # single/no-inheritance class
    multi_inherit = dynamic

    @property
    def static_from_dynamic(self):
        return len(self.bases) == 1 and self.bases[0].dynamic

    def has_multi_inherit_subclass(self):
        return any(c.multi_inherit or c.has_multi_inherit_subclass() for c in self.derived)

    def variable_storage(self):
        """Returns true if the memory layout for this object varies"""
        return self.has_multi_inherit_subclass() or self.indirect_features()

    def uninstantiatable(self):
        return (not self.constructor) and any(pure_virtual(m) for m in self.type.members)

    def findbases(self,classdefs):
        assert len(self.bases) == 0
        for b in self.type.bases:
            cd = classdefs.get(b.type)
            if cd:
                self.bases.append(cd)
                cd.derived.append(self)

    def check_needs_mode_var(self):
        """Checks if the mode variable will be required.

        A mode variable is required if this class needs to be stored any way
        other than the default (CONTAINS), if it can exist in an unitialized
        state (UNITIALIZED), or if any base or derived class needs the variable.

        Even with new-initializes="true", any class that has a constructor that
        can throw an exception and a non-trivial destructor needs a mode
        variable. If the constructor throws an exception, the allocated object
        memory needs to be freed using Py_DECREF. Since Py_DECREF causes the
        dealloc function to be called, the mode variable is required for that
        function to know when it must not call the destructor.

        """
        if (not self.needs_mode_var) and (self.features or not (self.new_initializes and self.no_destruct)):
            self.propogate_needs_mode_var()

    def propogate_needs_mode_var(self):
        self.needs_mode_var = True
        for c in itertools.chain(self.derived,self.bases):
            if not c.needs_mode_var:
                c.propogate_needs_mode_var()

    def cast_base_func(self):
        return tmpl.cast_base.render(
            type = self.type.typestr(),
            name = self.name,
            uninstantiatable = self.uninstantiatable(),
            features = self.indirect_features(),
            new_init = self.new_initializes)

    def get_base_func(self,module):
        """Generate the get_base_X(PyObject o) function.

        This function checks if the supplied object is the correct type and
        returns a reference to the wrapped type. If a subclass of the wrapped
        type inherits from more than one type, this function also checks if the
        wrapped type is one of the derived types and casts to it first, to allow
        the proper pointer fix-up to happen.

        """
        if self.has_multi_inherit_subclass():
            return self.heirarchy_chain().downcast_func()
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

    def output_special(self,name,out):
        f = self.special_methods.get(name)
        if not f: return False
        print >> out.cpp, f.output(out.conv)
        return True

    def rich_compare(self,out):
        if not any(self.special_methods.get(f) for f in
                   ('__lt__','__le__','__eq__','__ne__','__gt__','__ge__')):
            return False

        print >> out.cpp, tmpl.richcompare_start.format(
            name = self.name,
            prolog = self.method_prolog())

        for f,c in [
                ('__lt__','Py_LT'),
                ('__le__','Py_LE'),
                ('__eq__','Py_EQ'),
                ('__ne__','Py_NE'),
                ('__gt__','Py_GT'),
                ('__ge__','Py_GE')]:
            code = ''
            sf = self.special_methods.get(f)
            if sf:
                code = sf.function_call_1arg_fallthrough(out.conv,Tab(3))

            print >> out.cpp, tmpl.richcompare_op.format(op = c,code = code)

        print >> out.cpp, tmpl.richcompare_end

        return True

    def number(self,out):
        havenum = False

        for fn in ('__neg__','__pos__','__abs__','__invert__','__int__',
                   '__long__','__float__','__oct__','__hex__','__index__',
                   '__nonzero__'):
            if self.output_special(fn,out): havenum = True


        for fn in ('__iadd__','__isub__','__imul__','__idiv__','__itruediv__',
                '__ifloordiv__','__imod__','__ilshift__','__irshift__',
                '__iand__','__ixor__','__ior__'):
            f = self.special_methods.get(fn)
            if f:
                havenum = True
                print >> out.cpp, tmpl.function.format(
                    rettype = 'PyObject *',
                    name = 'obj_{0}_{1}'.format(self.name,fn),
                    args = 'obj_{0} *self,PyObject *arg'.format(self.name),
                    epilog = tmpl.ret_notimplemented,
                    code = self.method_prolog() + f.function_call_1arg_fallthrough(out.conv),
                    errval = '0')

        for fn in ('add__','sub__','mul__','floordiv__','mod__',
                'divmod__','lshift__','rshift__','and__','xor__',
                'or__','div__','truediv__'):
            f = self.special_methods.get('__'+fn)
            fr = self.special_methods.get('__r'+fn)
            if f or fr:
                havenum = True
                code = ''
                rcode = ''
                if f:
                    code = self.method_prolog('a',Tab(3)) + f.function_call_1arg_fallthrough(out.conv,ind=Tab(3),var='b')
                if fr:
                    rcode = self.method_prolog('b',Tab(3)) + fr.function_call_1arg_fallthrough(out.conv,ind=Tab(3),var='a')

                print >> out.cpp, tmpl.number_op.format(
                    cname = self.name,
                    op = '__'+fn,
                    args = 'PyObject *a,PyObject *b',
                    code = code,
                    rcode = rcode)

        f = self.special_methods.get('__ipow__')
        if f:
            havenum = True
            print >> out.cpp, tmpl.function.format(
                rettype = 'PyObject *',
                name = 'obj_{0}___ipow__'.format(self.name),
                args = 'obj_{0} *self,PyObject *arg1,PyObject *arg2'.format(self.name),
                epilog = tmpl.ret_notimplemented,
                code = self.method_prolog() + splitdefdef23code(f,out.conv,['arg1','arg2']),
                errval = '0')

        f = self.special_methods.get('__pow__')
        fr = self.special_methods.get('__rpow__')
        if f or fr:
            havenum = True
            code =''
            rcode = ''
            if f:
                code = self.method_prolog('a',Tab(3)) + splitdefdef23code(f,out.conv,vars=['b','c'],ind=Tab(3))
            if fr:
                rcode = self.method_prolog('b',Tab(3)) + fr.function_call_1arg_fallthrough(out.conv,ind=Tab(3),var='a')

            print >> out.cpp, tmpl.number_op.format(
                cname = self.name,
                op = '__pow__',
                args = 'PyObject *a,PyObject *b,PyObject *c',
                code = code,
                rcode = rcode)

        if havenum:
            print >> out.cpp, tmpl.number_methods.render(
                name = self.name,
                specialmethods = self.special_methods)

        return havenum

    def sequence(self,out):
        have = False

        for n in ('__sequence__len__','__concat__','__iconcat__','__contains__'):
            if self.output_special(n,out): have = True
            f = self.special_methods.get(n)

        for n in ('__repeat__','__irepeat__'):
            f = self.special_methods.get(n)
            if f:
                have = True
                bindpyssize(out.conv,f,'count')
                print >> out.cpp, tmpl.function.format(
                    rettype = 'PyObject *',
                    name = 'obj_{0}_{1}'.format(self.name,n),
                    args = 'obj_{0} *self,Py_ssize_t count'.format(self.name),
                    epilog = '',
                    code = self.method_prolog() + f.function_call_0arg(out.conv),
                    errval = '0')

        f = self.special_methods.get('__sequence__getitem__')
        if f:
            have = True
            bindpyssize(out.conv,f,'index')
            print >> out.cpp, tmpl.function.format(
                rettype = 'PyObject *',
                name = 'obj_{0}___sequence__getitem__'.format(self.name),
                args = 'obj_{0} *self,Py_ssize_t index'.format(self.name),
                epilog = '',
                code = self.method_prolog() + f.function_call_0arg(out.conv),
                errval = '0')

        f = self.special_methods.get('__sequence__setitem__')
        if f:
            have = True
            bindpyssize(out.conv,f,'index')
            print >> out.cpp, tmpl.function.format(
                rettype = 'int',
                name = 'obj_{0}___sequence__setitem__'.format(self.name),
                args = 'obj_{0} *self,Py_ssize_t index,PyObject *arg'.format(self.name),
                epilog = '',
                code = self.method_prolog() + f.function_call_1arg(out.conv),
                errval = '-1')

        if have:
            print >> out.cpp, tmpl.sequence_methods.render(
                name = self.name,
                specialmethods = self.special_methods)

        return have

    def mapping(self,out):
        have = False

        for fn in ('__mapping__len__','__mapping__getitem__','__mapping__setitem__'):
            if self.output_special(fn,out): have = True

        if have:
            print >> out.cpp, tmpl.mapping_methods.render(
                name = self.name,
                specialmethods = self.special_methods)

        return have

    def cast_base_expr(self):
        return ('get_base_{0}({{0}},false)' if self.has_multi_inherit_subclass() else 'cast_base_{0}({{0}})').format(self.name)

    def method_prolog(self,var='reinterpret_cast<PyObject*>(self)',ind=Tab(2)):
        return '{0}{1} &base = {2};\n'.format(
            ind,
            self.type.full_name,
            self.cast_base_expr().format(var))

    def constructor_args(self):
        return ({
            'args' : forwarding_args(args),
            'argvals' : forwarding_arg_vals(args)}
                for m in self.type.members
                    if isinstance(m,gccxml.CPPConstructor) and not varargs(m)
                for args in default_to_ov(m.args))

    def can_exist(self):
        """Return True if an instance with this exact type (a derived type
        doesn't count) can exist."""
        return self.features or not self.uninstantiatable()

    def instance_dict(self):
        return self._instance_dict and self.can_exist()

    def weakref(self):
        return self._weakref and self.can_exist()

    def indirect_features(self):
        """returns a union of features for this class and all derived classes"""
        return reduce(set.union,(d.indirect_features() for d in self.derived),self.features)

    def use_gc(self):
        return self._use_gc and self.can_exist()

    def gc_code(self,out):
        """Generate the garbage collection code if needed.

        This function also populates self.gc_fields which is needed by all
        derived classes.

        """
        use_t = False
        use_c = False

        gc_vars = []
        self.gc_fields = []

        if self.use_gc():
            self.gc_fields = qualified_fields(self.type,out.conv.cppclasstopy)

            if out.conv.gcvarhandler(self.type):
                gc_vars.append(('base',self.type))
                if self.gc_include:
                    err.emit_warning(SpecificationError('gc-include is ignored because <gc-handler> is defined for this type'))
                if self.gc_ignore:
                    err.emit_warning(SpecificationError('gc-ignore is ignored because <gc-handler> is defined for this type'))
                
                for f in recursive_qf_fields(self.gc_fields):
                    f.handle_gc = GC_FUNCTION
                    f.base_handler = self
                    
            else:
                for f in self.gc_include: qf_handle(self.gc_fields,True,f)
                for f in self.gc_ignore: qf_handle(self.gc_fields,False,f)
  
                for f in recursive_qf_fields(self.gc_fields):
                    if f.base_handler:
                        gc_vars.append(('static_cast<{0}&>(base)'.format(f.base_handler.type.type_str()),f.base_handler.type))
                    if f.handle_gc == GC_INCLUDE:
                        if not conv.gcvarhandler(f.type):
                            raise SpecificationError('There is no rule specifying how to garbage-collect an instance of "{0}". Please add one using <gc-handler>.'.format(f.type))
                        if any(sub_f.handle_gc == GC_INCLUDE for sub_f in recursive_qf_fields(f.components)):
                            emit_warning(RedundantSpecification('both "{0}" and one of its fields/items ("{1}") are marked as requiring garbage collection'.format(f.name,sub_f.name)))
                        #if f.access != gccxml.ACCESS_PUBLIC:
                        #    raise SpecificationError('"{0}" cannot be accessed for garbage collection because it is not public')
                        gc_vars.append(('base.'+f.name,f.type))
                    elif f.handle_gc == GC_FUNCTION:
                        base_handlers.add(f.base_handler)
                    elif (not f.handle_gc) and out.conv.gcvarhandler(f.type):
                        # Members that are not explicitly accepted or rejected
                        # are accepted if they are public.

                        # We don't have to worry about this being a sub-field of
                        # an accepted field because sub-fields are only added
                        # when specified explicitly (ie: bool(f.handle_gc) is
                        # True).
                        if f.access == gccxml.ACCESS_PUBLIC:
                            gc_vars.append(('base.'+f.name,f.type))
                        else:
                            err.emit_warning(SpecificationError(
                                ('"{0}" may need garbage collection but cannot be '+
                                'accessed because it is not public. Add "{0}" to ' +
                                'gc-ignore to prevent this warning.').format(f.name)))


            if self._instance_dict or gc_vars:
                t_body = ''
                c_body = ''

                if self._instance_dict:
                    t_body += tmpl.traverse_pyobject.format('self->idict')

                traverse = []
                clear = []
                getbase = None

                if gc_vars:
                    getbase = '    {0} &base = {1};\n'.format(self.type.typestr(),self.cast_base_expr().format('reinterpret_cast<PyObject*>(self)'))
                    for name,type in gc_vars:
                        t,c = out.conv.gcvarhandler(type)
                        traverse.append(t.format(name))
                        if c:
                            clear.append(c.format(name))

                    if not self.new_initializes:
                        t_body += '    if(self->mode) {\n'

                    t_body += getbase
                    t_body += ''.join(traverse)

                    if not self.new_initializes:
                        t_body += '    }\n'


                if self._instance_dict:
                    c_body += tmpl.clear_pyobject.format('self->idict')

                if clear:
                    if not self.new_initializes:
                        c_body += '    if(self->mode) {\n'

                    c_body += getbase
                    c_body += ''.join(clear)

                    if not self.new_initializes:
                        c_body += '    }\n'


                use_t = True
                print >> out.cpp, tmpl.traverse_shell.format(self.name,t_body)
                if c_body:
                    use_c = True
                    print >> out.cpp, tmpl.clear_shell.format(self.name,c_body)

        return use_t,use_c


    @append_except
    def output(self,out,module):
        has_mi_subclass = self.has_multi_inherit_subclass()

        # If this is a statically declared class and its base is a dynamic
        # class, don't set tp_base yet (we don't have an address for it yet).
        bases = []
        if not self.static_from_dynamic:
            if self.bases:
                bases = ['get_obj_{0}Type()'.format(b.name) for b in self.bases]
            elif has_mi_subclass:
                # common type needed for multiple inheritance
                bases = ['&_obj_Internal{0}Type'.format(EXTRA_VARS_SUFFIXES[(self._weakref << 1) | self._instance_dict])]

        virtmethods = []
        typestr = self.type.typestr()
        for m in self.methods:
            for o in m.overloads:
                if is_virtual(o.func) and o.bridge_virt:
                    virtmethods.append((m,o.func))

        if virtmethods:
            print >> out.h, tmpl.subclass.render(
                name = self.name,
                type = typestr,
                constructors = self.constructor_args(),
                methods = ({
                    'name' : m.canon_name,
                    'ret' : m.returns.typestr(),
                    'args' : forwarding_args(m.args),
                    'const' : m.const}
                        for d,m in virtmethods))

            typestr += '_virt_handler'


        richcompare = self.rich_compare(out)
        number = self.number(out)
        mapping = self.mapping(out)
        sequence = self.sequence(out)


        destructor = None
        d = self.type.getDestructor()
        if (not (self.no_destruct or self.uninstantiatable())) and d:
            # the destructor's name is not typestr when the type is a template instance
            destructor = d.canon_name
            print >> out.cpp, tmpl.destruct.render(
                name = self.name,
                destructor = destructor,
                features = self.features,
                new_init = self.new_initializes,
                instance_dict = self.instance_dict(),
                weakref = self.weakref()),


        print >> out.h, tmpl.classdef.render(
            name = self.name,
            type = typestr,
            original_type = self.type.typestr(),
            destructor = destructor,
            uninstantiatable = self.uninstantiatable(),
            bool_arg_get = self.has_multi_inherit_subclass(),
            new_init = self.new_initializes,
            dynamic = self.dynamic,
            features = self.features,
            constructors = self.constructor_args(),
            invariable = not self.variable_storage(),
            instance_dict = self.instance_dict(),
            weakref = self.weakref(),
            mode_var = self.needs_mode_var,
            gc = self.use_gc()),

        if virtmethods:
            print >> out.h, tmpl.subclass_meth.render(name=self.name)


        getsettable = []
        memberstable = []
        for v in self.vars:
            print >> out.cpp, v.output(out.conv)
            getset,entry = v.table_entry(out.conv)
            (getsettable if getset else memberstable).append(entry)

        getsetref = False
        if self.properties or getsettable:
            for p in self.properties:
                print >> out.cpp, p.output(out.conv),

            print >> out.cpp,  tmpl.getset_table.format(
                name = self.name,
                items = ',\n    '.join(itertools.chain((p.table_entry() for p in self.properties),getsettable))),

            getsetref = True

        membersref = False
        if memberstable:
            print >> out.cpp, tmpl.member_table.format(
                name = self.name,
                items = ',\n    '.join(memberstable)),
            membersref = True


        methodsref = False
        if self.methods:
            tentries,bodies = zip(*[m.output(out.conv) for m in self.methods])
            for b in bodies:
                print >> out.cpp, b,

            print >> out.cpp, tmpl.method_table.format(
                name = self.name,
                items = ',\n    '.join(tentries)),

            methodsref = True

        for fn in ('__cmp__','__repr__','__hash__','__call__','__str__','__getattr__','__setattr__','__iter__','next'):
            self.output_special(fn,out)

        if virtmethods:
            for d,m in virtmethods:
                try:
                    (frompy,rettype) = out.conv.frompy(m.returns) if m.returns != out.conv.void else (None,None)
                    print >> out.cpp, tmpl.virtmethod.render(
                        cname = self.name,
                        name = d.name,
                        pure = m.pure_virtual,
                        ret = m.returns.typestr(),
                        func = m.canon_name,
                        const = m.const,
                        type = self.type.typestr(),
                        args = forwarding_args(m.args),
                        argvals = forwarding_arg_vals(m.args),
                        pyargvals = ''.join(out.conv.topy(a.type).format('_{0}'.format(i)) + ',' for i,a in enumerate(m.args)),
                        retfrompy = frompy and frompy.format('ret'),
                        rettype = rettype and rettype.typestr())
                except err.Error as e:
                    e.info['method'] = d.name
                    raise


        gc,clear = self.gc_code(out)


        print >> out.cpp, tmpl.classtypedef.render(
            dynamic = self.dynamic,
            name = self.name,
            type = typestr,
            features = self.features,
            new_init = self.constructor and self.new_initializes,
            destructor = destructor,
            initcode = self.constructor and self.constructor.output(out.conv,typestr),
            module = module.name,
            doc = self.doc,
            getsetref = getsetref,
            membersref = membersref,
            methodsref = methodsref,
            richcompare = richcompare,
            number = number,
            mapping = mapping,
            sequence = sequence,
            bases = bases,
            derived = [d.name for d in self.derived],
            specialmethods = self.special_methods,
            instance_dict = self.instance_dict(),
            weakref = self.weakref(),
            gc = gc,
            gc_clear = clear),


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
    """This does not test for the "restrict" qualifier (because the code that this program generates never aliases mutable pointers)."""
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
            func.output(((c or conv.frompy(a.type)[0]).format(get_arg(i)) for
                i,a,c in zip(itertools.count(),args,argconv)),ind+1))

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



def deref_placeholder(x):
    return '*({0})' if isinstance(x,gccxml.CPPPointerType) else '{0}'

class Conversion:
    def __init__(self,tns):
        # get the types specified by the typedefs
        for x in ("bool","sint","uint","sshort","ushort","slong","ulong",
                  "float","double","long_double","size_t","py_ssize_t","schar",
                  "uchar","char","wchar_t","py_unicode","void","stdstring",
                  "stdwstring","pyobject",'visitproc'):
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


        fl = "PyInt_FromLong({0})"
        ful = "PyLong_FromUnsignedLong({0})"
        fd = "PyFloat_FromDouble({0})"

        self.__topy = {
            self.bool : 'bool_to_py({0})',
            self.sshort : fl,
            self.ushort : fl,
            self.sint : fl,
            self.uint : 'uint_to_py({0})',
            self.slong : fl,
            self.ulong : ful,
            self.float : fd,
            self.double : fd,
            self.long_double : 'PyFloat_FromDouble(static_cast<double>({0}))',
            self.pyobject : '{0}',
            self.stdstring : 'string_to_py({0})',
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


        tod = (False,'py_to_double({0})')
        # The first value of each tuple specifies whether the converted type is
        # a reference to the original value. If not, it cannot be passed by
        # non-const reference.
        self.__frompy = {
            self.bool : (False,'static_cast<bool>(PyObject_IsTrue({0}))'),
            self.sshort : (False,'py_to_short({0})'),
            self.ushort : (False,'py_to_ushort({0})'),
            self.sint : (False,'py_to_int({0})'),
            self.uint : (False,'py_to_uint({0})'),
            self.slong : (False,'py_to_long({0})'),
            self.ulong : (False,'py_to_ulong({0})'),
            self.float : (False,'static_cast<float>(py_to_double({0}))'),
            self.double : tod,
            self.long_double : tod,
            self.pyobject : (True,'{0}'),
            self.cstring : (False,'PyString_AsString({0})')
        }

        self.from_py_ssize_t = {
            self.schar : 'py_ssize_t_to_schar({0})',
            self.uchar : 'py_ssize_t_to_uchar({0})',
            self.char : 'py_ssize_t_to_char({0})',
            self.sshort : 'py_ssize_t_to_sshort({0})',
            self.ushort : 'py_ssize_t_to_ushort({0})',
            self.sint : 'py_ssize_t_to_ssint({0})',
            self.uint : 'py_ssize_t_to_uint({0})',
            self.slong : '{0}',
            self.ulong : 'py_ssize_t_to_ulong({0})'
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
            self.py_ssize_t : 'T_PYSSIZET'
        }

        self.integers = set((self.sint,self.uint,self.sshort,self.ushort,
            self.slong,self.ulong,self.size_t,self.py_ssize_t,self.schar,
            self.uchar,self.char))

        if self.slonglong:
            self.__topy[self.slonglong] = 'PyLong_FromLongLong({0})'
            self.__topy[self.ulonglong] = 'PyLong_FromUnsignedLongLong({0})'

            self.basic_types[TYPE_LONG].add(self.slonglong)
            self.basic_types[TYPE_LONG].add(self.ulonglong)

            self.__frompy[self.slonglong] = (False,'py_to_longlong({0})')
            self.__frompy[self.ulonglong] = (False,'py_to_ulonglong({0})')

            self.from_py_ssize_t[self.slonglong] = '{0}'

            # to_ulong is used since 'Py_ssize_t' isn't going to be larger than 'long' anyway
            self.from_py_ssize_t[self.ulonglong] = 'py_ssize_t_to_ulong({0})'

            self.__pymember[self.slonglong] = 'T_LONGLONG'
            self.__pymember[self.ulonglong] = 'T_ULONGLONG'

            self.integers.add(self.slonglong)
            self.integers.add(self.ulonglong)

        self.cppclasstopy = {}

        self.__gcvarhandlers = {
            self.pyobject : (tmpl.traverse_pyobject,tmpl.clear_pyobject)
        }

    @staticmethod
    def __find_conv(name,check,static,x):
        if isinstance(x,gccxml.CPPClass):
            funcs = x.lookup(name)
            if funcs and isinstance(funcs[0],gccxml.CPPMethod):
                # find a usable overload
                for f in funcs:
                    if check(f):
                        if f.static == static:
                            return f

                        err.emit_warning(SpecificationError('"{0}" has a method named {1} but it can\'t be used because it\'s {2}static'.format(x.name,name,['not ',''][static])))
                        break
                else:
                    err.emit_warning(SpecificationError('"{0}" has a method named {1} but is has the wrong format'.format(x.name,name)))
        return None

    def __topy_base(self,t):
        """Look up a template for converting t to "PyObject*".

        If one isn't found in self.__topy, check if t has a member function with
        the name given by "TO_PY_FUNC".
        """
        try:
            return self.__topy[t]
        except KeyError:
            r = None
            if Conversion.__find_conv(
                  TO_PY_FUNC,
                  (lambda f: f.returns == self.pyobject and accepts_args(f,[])),
                  False,
                  t):
                r = '({{0}}).{0}()'.format(TO_PY_FUNC)

            # save the value to avoid searching again and triggering the same
            # warnings
            self.__topy[t] = r
            return r

    def __topy_pointee(self,x):
        return self.__topy_base(strip_cvq(x.type))

    def topy(self,origt,retsemantic = None,container = None,temporary = True):
        t = strip_cvq(origt)

        # if the value is not a temporary, we can store a reference to it, even
        # if the value itself is not a reference
        if retsemantic == RET_UNMANAGED_REF and not temporary:
            classdef = self.cppclasstopy.get(strip_refptr(t))
            if classdef:
                return tmpl.new_uref.format(classdef[0].name,deref_placeholder(t))


        r = self.__topy_base(t)
        if r: return r

        if isinstance(t,gccxml.CPPArrayType):
            # array types are implicitly convertable to pointer types
            r = self.__topy.get(cptr(cconst(t.type) if is_const(origt) else t.type))
            if r: return r
        elif isinstance(t,(gccxml.CPPPointerType,gccxml.CPPReferenceType)):
            if retsemantic == RET_COPY:
                r = self.__topy_pointee(t)
                if r:
                    if isinstance(t,gccxml.CPPPointerType):
                        r = r.format('*({0})')
                    return r
            else:
                classdef = self.cppclasstopy.get(strip_cvq(t.type))
                if classdef:
                    if retsemantic == RET_MANAGED_REF:
                        assert container
                        return 'reinterpret_cast<PyObject*>(new ref_{0}({2},reinterpret_cast<PyObject*>({1})))'.format(
                            classdef[0].name,
                            container,
                            deref_placeholder(t))
                    elif retsemantic == RET_UNMANAGED_REF:
                        return tmpl.new_uref.format(
                            classdef[0].name,
                            deref_placeholder(t))
                    elif retsemantic == RET_MANAGED_PTR:
                        return 'reinterpret_cast<PyObject*>(new ptr_{0}({1}))'.format(
                            classdef[0].name,
                            '{0}' if isinstance(t,gccxml.CPPPointerType) else '&({0})') # taking the address of a reference in order to call delete (eventually) is weird, but whatever

        raise SpecificationError('No conversion from "{0}" to "PyObject*" is registered'.format(t.typestr()))

    def requires_ret_semantic(self,origt,feature,temporary=True):
        t = strip_cvq(origt)
        if (feature == RET_UNMANAGED_REF and not temporary) or (isinstance(t,(gccxml.CPPPointerType,gccxml.CPPReferenceType)) and not self.__topy_base(t)):
            retc = self.cppclasstopy.get(strip_cvq(strip_refptr(t)))
            if retc:
                retc[0].features.add(feature)
                return True
        return False

    def __frompy_base(self,t):
        """Look up a template for converting "PyObject* to t".

        If one isn't found in self.__frompy, check if t has a member function with
        the name given by "FROM_PY_FUNC".
        """
        try:
            return self.__frompy[t]
        except KeyError:
            r = None
            f = Conversion.__find_conv(
                FROM_PY_FUNC,
                (lambda f: accepts_args(f,[self.pyobject])
                 and strip_cvq(f.returns.type
                               if isinstance(f.returns,gccxml.CPPReferenceType)
                               else f.returns) == t),
                True,
                t)
            if f:
                r = ((isinstance(f.returns,gccxml.CPPReferenceType)
                        and not is_const(f.returns.type)),
                    '{0}::{1}({{0}})'.format(t.full_name,FROM_PY_FUNC))

            # save the value to avoid searching again and triggering the same
            # warnings
            self.__frompy[t] = r
            return r

    def frompy(self,t):
        """Returns a tuple containing the conversion code string and the type
        (CPP_X_Type) that the code returns"""

        assert isinstance(t,gccxml.CPPType)

        r = self.__frompy_base(t)
        ref = lambda x: gccxml.CPPReferenceType(x) if r[0] else x
        if r: return r[1],ref(t)

        # check if t is a pointer or reference to a type we can convert
        if isinstance(t,gccxml.CPPReferenceType):
            nt = strip_cvq(t.type)
            r = self.__frompy_base(nt)
            if r and (r[0] or const_qualified(t.type)):
                return r[1], ref(nt)
        elif isinstance(t,gccxml.CPPPointerType):
            nt = strip_cvq(t.type)
            r = self.__frompy_base(nt)
            if r and(r[0] or const_qualified(t.type)):
                return '*({0})'.format(r[1]), ref(nt)
        elif isinstance(t,gccxml.CPPCvQualifiedType):
            r = self.__frompy_base(t.type)
            if r: return r[1], ref(t.type)

        raise SpecificationError('No conversion from "PyObject*" to "{0}" is registered'.format(t.typestr()))

    def gcvarhandler(self,t):
        try:
            return self.__gcvarhandlers[t]
        except KeyError:
            r = None
            if Conversion.__find_conv(
                  TRAVERSE_FUNC,
                  (lambda f: f.returns == self.sint 
                      and accepts_args(
                          f,
                          [self.visitproc,gccxml.CPPPointerType(self.void)])),
                  False,
                  t):
                clear = None
                if Conversion.__find_conv(
                      CLEAR_FUNC,
                      (lambda f: f.returns == self.void and accepts_args(f,[])),
                      False,
                      t):
                    clear = '    ({{0}}).{0}();'.format(CLEAR_FUNC)
                
                r = tmpl.traverse_t_func.format(TRAVERSE_FUNC),clear

            # save the value to avoid searching again and triggering the same
            # warnings
            self.__gcvarhandlers[t] = r
            return r

    def member_macro(self,t):
        try:
            return self.__pymember[t]
        except KeyError:
            r = None
            m = t.lookup(CAST_AS_MEMBER_FIELD)
            if m and isinstance(m[0],gccxml.CPPField):
                r = '{0}::{1}'.format(t.full_name,CAST_AS_MEMBER_FIELD)

            self.__pymember[t] = r
            return r

    def check_and_cast(self,t):
        st = strip_refptr(t)
        try:
            cdef,cast = self.cppclasstopy[st]
        except KeyError:
            raise SpecificationError('No conversion from "PyObject*" to "{0}" is registered'.format(st.typestr()))

        check ='PyObject_TypeCheck({{0}},get_obj_{0}Type())'.format(cdef.name)
        if isinstance(t,gccxml.CPPPointerType): cast = '&' + cast
        return check,cast

    def arg_parser(self,args,use_kwds = True,indent = Tab(2)):
        # even if we are not taking any arguments, get_arg::finish should still be called (to report an error if arguments were received)

        prep = indent.line('get_arg ga(args,{1});'.format(indent,'kwds' if use_kwds else '0'))

        namesvar = '0'
        if use_kwds and any(a.name for a in args):
            prep += indent.line('const char *names[] = {{{0}}};'.format(
                ','.join(('"{0}"'.format(a.name) if a.name else '0') for a in args)))
            namesvar = 'names'

        if args:
            prep += indent.line('PyObject *temp;')

        for i,a in enumerate(args):
            frompy, frompytype = self.frompy(a.type)
            var = frompytype.typestr("_{0}".format(i))
            name = 'names[{0}]'.format(i) if a.name and use_kwds else '0'
            if a.default:
                defval = a.default
                if isinstance(a.type,gccxml.CPPReferenceType) and cconst(a.type.type):
                    # if the argument takes a const reference, the default value
                    # is very likely to be a temporary, so we need to save it
                    # to a variable
                    prep += indent.line('{0} temp{1} = {2};'.format(a.type.type.type.typestr(),i,a.default))
                    defval = 'temp{0}'.format(i)
                prep += '{0}temp = ga({1},false);\n{0}{2} = temp ? {3} : {4};\n'.format(indent,name,var,frompy.format('temp'),defval)
            else:
                # we put the PyObject in a variable in case frompy has more than one '{0}'
                prep += '{0}temp = ga({1},true);\n{0}{2} = {3};\n'.format(indent,name,var,frompy.format('temp'))

        prep += indent.line('ga.finished({0});'.format(namesvar))

        return ['_{0}'.format(i) for i in range(len(args))], prep

    def arg_parser_b(self,args,use_kwds=True,indent=Tab(2)):
        prep = ''
        if any(a.default for a in args):
            convargs = [(i,a) + self.frompy(a.type) for i,a in enumerate(args)]

            for a in args:
                t = a.type
                if isinstance(t,gccxml.CPPReferenceType) and cconst(t.type):
                    # if the argument takes a const reference, the default value
                    # is very likely to be a temporary
                    t = t.type.type

                prep += indent.line('{0} _{1};'.format(t.typestr(),i,' = {2}'.format(a.default) if a.default else ''))

            prep += indent.line('switch(PyTuple_GET_SIZE(args)) {')
            i2 = indent + 1
            case = 'case {0}:'
            prep += indent.line(case.format(len(args)))
            for i,a in reversed(list(enumerate(args))):
                prep += i2.line('_{0} = {1}'.format(i,frompy.format('PyTuple_GET_ITEM(args,{0})'.format(i))))
                if a.default:
                    prep += indent.line(case.format(i))
            prep += '{0}    break;\n{0}default:\n{0}}}\n'

        return ['_{0}'.format(i) for i in range(len(args))], prep

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
            args,prep = self.arg_parser(calls[0][1],use_kwds)
            return prep + Tab(2).line(calls[0][0].output(args,Tab(2)))

        # turn default values into overloads
        ovlds = []
        for f,args in calls:
            ovlds.extend((f,newargs) for newargs in default_to_ov(args))

        return tmpl.overload_func_call.render(
            inner = self.generate_arg_tree(ovlds).get_code(self),
            nokwdscheck = use_kwds,
            args = 'args',
            errval = errval)

    def function_call_narg_fallthrough(self,calls,vars,ind=Tab(2)):
        assert calls
        return self.generate_arg_tree(calls).basic_and_objects_code(
            self,[],len(vars)-1,ind,lambda x: vars[x],True)

    def function_call_narg(self,calls,vars,errval='0',ind=Tab(2)):
        if len(calls) == 1:
            return ind + calls[0][0].output(
                [self.frompy(a.type)[0].format(v) for a,v in zip(calls[0][1],vars)],
                ind)

        return tmpl.overload_func_call.render(
            inner = self.function_call_narg_fallthrough(calls,vars,ind),
            nokwdscheck = False,
            args = ','.join(vars),
            errval = errval,
            endlabel = False)

    def add_conv(self,t,to=None,from_=None):
        if to: self.__topy[t] = to
        if from_: self.__frompy[t] = from_

    def add_gcvarhandler(self,t,handlers):
        self.__gcvarhandlers[t] = handlers

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


def methods_that_return(c):
    return itertools.chain(((m.name,m) for m in c.methods),((p.name,p.get) for p in c.properties if p.get))


class SmartPtr:
    def __init__(self):
        self.to = None
        self.from_ = None
        self.type = None
        self.uniquenum = get_unique_num()

    def gccxml_input(self,outfile,classes):
        assert self.type

        for c in classes:
            print >> outfile, 'typedef {0} smartptr_{1}_{2};\n'.format(self.type.format(c.type),c.uniquenum,self.uniquenum)

    def get_type(self,tns,class_):
        return tns.find('smartptr_{0}_{1}'.format(class_.uniquenum,self.uniquenum))[0]


def check_extra_vars(s_weakref,s_instance_dict,c,s_multi,bases_needed):
    if c._weakref and not s_weakref:
        raise SpecificationError('a class cannot omit weak reference support if any of its base classes include weak reference support')

    if c._instance_dict and not s_instance_dict:
        raise SpecificationError('a class cannot omit an instance dictionary if any of its base classes include an instance dictionary')

    # these next two requirements are strange but an "instance lay-out conflict"
    # error occurs otherwise, when importing the resulting module
    if s_multi:
        if c._weakref != s_weakref:
            raise SpecificationError("a class that includes weak reference support and uses multiple-inheritance cannot have any base classes that don't include weak reference support")

        if c._instance_dict != s_instance_dict:
            raise SpecificationError("a class that includes an instance dictionary and uses multiple-inheritance cannot have any base classes that don't include an instance dictionary")

    if c.multi_inherit:
        bases_needed[(c._weakref << 1) + c._instance_dict] = True

    for b in c.bases:
        check_extra_vars(c._weakref,c._instance_dict,b,s_multi or c.multi_inherit,bases_needed)

class ModuleDef:
    def __init__(self,name,includes=None):
        self.name = name
        self.includes = includes or []
        self.classes = []
        self.functions = {}
        self.doc = ''
        self.topy = []
        self.frompy = []
        self.smartptrs = []
        self.vars = {}
        self.gchandlers = []

    def print_gccxml_input(self,out):
        # In addition to the include files, declare certain typedefs so they can
        # be matched against types used elsewhere
        print >> out, tmpl.gccxmlinput_start.format(self._formatted_includes(),TEST_NS)

        for c in self.classes:
            c.gccxml_input(out)

        for f in self.functions.itervalues():
            f.gccxml_input(out)

        for s in self.smartptrs:
            s.gccxml_input(out,self.classes)

        for v in self.vars.itervalues():
            v.gccxml_input(out)

        for i,conv in enumerate(self.topy):
            print >> out, 'typedef {0} topy_type_{1};\n'.format(conv[0],i)

        for i,conv in enumerate(self.frompy):
            print >> out, 'typedef {0} frompy_type_{1};\n'.format(conv[0],i)

        for i,handler in enumerate(self.gchandlers):
            print >> out, 'typedef {0} gchandler_type_{1};\n'.format(handler[0],i)

        print >> out, '}\n'

        for c in self.classes:
            # instantiate templates
            if c.template:
                print >> out, 'template class {0};\n'.format(c.type)

    def _formatted_includes(self):
        return "\n".join('#include "{0}"'.format(i) for i in self.includes)

    def write_file(self,path,scope):
        tns = scope.find(TEST_NS)[0]
        conv = Conversion(tns)

        for i,to in enumerate(self.topy):
            conv.add_conv(tns.find('topy_type_{0}'.format(i))[0],to=to[1])

        for i,from_ in enumerate(self.frompy):
            conv.add_conv(
                tns.find('frompy_type_{0}'.format(i))[0],
                from_=(False,from_[1]))

        for i,handler in enumerate(self.gchandlers):
            conv.add_gcvarhandler(
                tns.find('gchandler_type_{0}'.format(i))[0],
                handler[1:])

        out = Output(
            open(os.path.join(path, self.name + '.cpp'),'w'),
            open(os.path.join(path, self.name + '.h'),'w'),
            conv)

        print >> out.cpp, tmpl.module_start.format(
            includes = self._formatted_includes(),
            module = self.name)

        print >> out.h, tmpl.header_start.render(module = self.name)


        classes = {}

        for cdef in self.classes:
            c = TypedClassDef(scope,cdef,tns)
            classes[c.type] = c

            # these assume the class has copy constructors
            conv.add_conv(c.type,'reinterpret_cast<PyObject*>(new obj_{0}({{0}}))'.format(c.name),(True,'get_base_{0}({{0}})'.format(c.name)))
            conv.cppclasstopy[c.type] = c,c.cast_base_expr()

        for s in self.smartptrs:
            for c in classes.itervalues():
                t = s.get_type(tns,c)
                conv.add_conv(
                    t,
                    s.to and s.to.format('{0}',c.type.typestr()),
                    s.from_ and (False,s.from_.format('get_base_{0}({{0}})'.format(c.name),c.type.typestr(),'{0}')))
                if s.from_:
                    conv.cppclasstopy[t] = c,s.from_.format(c.cast_base_expr(),c.type.typestr(),'{0}')

        for c in classes.itervalues():
            c.findbases(classes)


        # Sort classes by heirarchy. Base classes need to be declared before derived classes.
        classes = sorted(classes.itervalues(),key=TypedClassDef.basecount)

        functions = [TypedDefDef(scope,f,tns) for f in self.functions.itervalues()]
        vars = [TypedVarDef(scope,v,tns) for v in self.vars.itervalues()]


        # find all methods and functions that return objects that require special storage
        for c in classes:
            for name,m in methods_that_return(c):
                for ov in m.overloads:
                    if ov.retsemantic in (RET_MANAGED_REF,RET_MANAGED_PTR,RET_UNMANAGED_REF):
                        conv.requires_ret_semantic(ov.func.returns,ov.retsemantic)

            for v in c.vars:
                if v.really_a_property(conv):
                    try:
                        conv.requires_ret_semantic(v.getter_type(conv),RET_MANAGED_REF)
                    except err.Error as e:
                        e.info['attr'] = v.name

        for f in functions:
            for ov in f.overloads:
                if ov.retsemantic in (RET_MANAGED_PTR,RET_UNMANAGED_REF):
                    conv.requires_ret_semantic(ov.func.returns,ov.retsemantic)

        for v in vars:
            if v.ref in (RET_MANAGED_PTR,RET_UNMANAGED_REF):
                conv.requires_ret_semantic(v.type,v.ref,v.temporary)


        bases_needed = [False] * 4

        for c in classes:
            c.check_needs_mode_var()

            # any method that is redefined in a subclass needs to be re-exposed
            # (even virtual methods because they are always called with a
            # type-qualifier).
            for m in c.methods:
                for d in c.derived:
                    if not any(m.name == dm.name for dm in d.methods):
                        newm = False
                        newo = []
                        for o in m.overloads:
                            if isinstance(o.func,gccxml.CPPMethod) and o.func.context is not d.type.find(o.func.canon_name)[0].context:
                                newm = True
                                no = copy.copy(o)
                                no.func = copy.copy(o.func)
                                no.func.pure_virtual = False
                                newo.append(no)
                            else:
                                newo.append(o)

                        if newm:
                            newm = copy.copy(m)
                            newm.overloads = newo
                            newm.classdef = d
                            d.methods.append(newm)

            check_extra_vars(True,True,c,False,bases_needed)


        for combo,suffix in enumerate(EXTRA_VARS_SUFFIXES):
            if bases_needed[combo]:
                print >> out.cpp, tmpl.obj_internal.render(
                    module=self.name,
                    weakref=combo & 2,
                    instance_dict=combo & 1,
                    suffix=suffix)


        # TODO: These functions result in the same machine code as long as the
        # types have the same alignment. They can probably be replaced by a
        # single function.
        for c in classes:
            print >> out.cpp, c.cast_base_func()

        for c in classes:
            print >> out.cpp, c.get_base_func(self)

        for c in classes:
            c.output(out,self)

        functable = []
        for f in functions:
            tentry,body = f.output(conv)
            print >> out.cpp, body
            functable.append(tentry)


        print >> out.cpp, tmpl.module.render(
            funclist = functable,
            module = self.name,
            doc = self.doc,
            classes = [{
                'name' : c.name,
                'dynamic' : c.dynamic,
                'new_init' : c.new_initializes,
                'no_init' : not c.constructor,
                'base' : c.static_from_dynamic and c.bases[0].name}
                    for c in classes],
            vars = ({'name' : v.name,'create' : v.creation_code(conv)} for v in vars),
            internal_suffixes = [s for need,s in zip(bases_needed,EXTRA_VARS_SUFFIXES) if need]
        )

        print >> out.h, tmpl.header_end


class VarDef:
    def __init__(self,value,name,ref):
        self.value = value
        self.name = name
        self.ref = ref
        self.uniquenum = get_unique_num()

    def gccxml_input(self,outfile):
        # This uses the GCC __typeof__ keyword. If a parser other than GCCXML is
        # used, another means of getting the type of the expression will have to
        # be used (such as getting the return type of an instantiation of
        # template<typename T> T &dummy_func(T &); ).
        print >> outfile, 'typedef __typeof__({0}) var_type_{1};\n'.format(self.value,self.uniquenum)

    def get_type(self,tns):
        return tns.find('var_type_{0}'.format(self.uniquenum))[0]

class TypedVarDef:
    def __init__(self,scope,vardef,tns):
        self.value = vardef.value
        self.name = vardef.name
        self.ref = vardef.ref
        self.type = vardef.get_type(tns)

        try:
            # no need to check if self.value is a legal C++ identifier. If it
            # isn't, we simply wont find anything with that name.
            self.temporary = not isinstance(scope.find(self.value)[0],gccxml.CPPVariable)
        except SpecificationError:
            self.temporary = True

    def creation_code(self,conv):
        return conv.topy(self.type,self.ref,temporary=self.temporary).format(self.value)



pykeywords = frozenset((
    'and','as','assert','break','class','continue','def','del','elif','else',
    'except','exec','finally','for','from','global','if','import','in','is',
    'lambda','not','or','pass','print','raise','return','try','while','with',
    'yield'))

def get_valid_py_ident(x,backup):
    if x:
        m = re.match(r'[a-zA-Z_]\w*',x)
        if not (m and m.end() == len(x)):
            raise SpecificationError('"{0}" is not a valid Python identifier'.format(x))
        if x in pykeywords:
            raise SpecificationError('"{0}" is a reserved identifier'.format(x))
        return x
    if backup in pykeywords:
        return backup + '_'
    return re.sub('\W','_',backup)


def join_func(cur,new):
    if new.doc:
        if cur.doc:
            raise SpecificationError("<doc> was defined twice for the same function/method")
        cur.doc = new.doc
    cur.overloads.extend(new.overloads)

def add_func(x,func):
    cur = x.get(func.name)
    if cur:
        join_func(cur,func)
    else:
        x[func.name] = func


def stripsplit(x):
    return [i.strip() for i in x.split(',')]




class tag_Init(tag):
    def __init__(self,args):
        self.r = DefDef()
        self.r.overloads.append(Overload(args=args.get("overload")))


class tag_ToFromPyObject(tag):
    def __init__(self,args):
        self.r = ''

    def text(self,data):
        self.r += data

    @tag_handler('val',tag)
    def handle_val(self,data):
        self.r += '{0}'

    @tag_handler('type',tag)
    def handle_type(self,data):
        self.r += '{1}'


class tag_ToFromPyObjectWithType(tag_ToFromPyObject):
    def __init__(self,args):
        super(tag_ToFromPyObjectWithType,self).__init__(args)
        self.type = args['type']

    def end(self):
        return self.type,self.r


class tag_ToFromPyObjectWithPyObject(tag_ToFromPyObject):
    @tag_handler('pyobject',tag)
    def handle_pyobject(self,data):
        self.r += '{2}'

class tag_PtrType(tag):
    def __init__(self,args):
        self.r = ''

    def text(self,data):
        self.r += data

    @tag_handler('type',tag)
    def handle_type(self,data):
        self.r += '{0}'


class tag_SmartPtr(tag):
    def __init__(self,args):
        self.r = SmartPtr()

    @tag_handler('ptr-type',tag_PtrType)
    def handle_ptrtype(self,data):
        if self.r.type:
            raise ParseError('<smart-ptr> can only have one <ptr-type>')
        self.r.type = data

    @tag_handler('to-pyobject',tag_ToFromPyObject)
    def handle_topyobject(self,data):
        self.r.to = data

    @tag_handler('from-pyobject',tag_ToFromPyObject)
    def handle_frompyobject(self,data):
        self.r.from_ = data

    def end(self):
        if not self.r.type:
            raise SpecificationError('<ptr-type> must be defined for <smart-ptr>')
        if not (self.r.to or self.r.from_):
            raise SpecificationError('<to-pyobject> or <from-pyobject> must be defined for <smart-ptr>')
        return self.r


class tag_Doc(tag):
    def __init__(self,args):
        self.r = ''

    def text(self,data):
        self.r = textwrap.dedent(data)


def getset_or_none(x):
    if not x: return None
    r = DefDef()
    r.overloads.append(Overload(x))
    return r


class tag_GetSet(tag):
    def __init__(self,args):
        self.r = DefDef()
        self.r.overloads.append(Overload(
            tag_Def.operator_parse(args['func']),
            get_ret_semantic(args),
            args.get('overload')))


class tag_Property(tag):
    def __init__(self,args):
        self.r = PropertyDef(
            args["name"],
            getset_or_none(args.get("get")),
            getset_or_none(args.get("set")))

    def end(self):
        if not (self.r.get or self.r.set):
            raise SpecificationError("property defined with neither a getter nor a setter")
        return self.r

    @tag_handler('doc',tag_Doc)
    def handle_doc(self,data):
        self.r.doc = data

    @tag_handler('get',tag_GetSet)
    def handle_get(self,data):
        if self.r.get:
            # only one get is allowed since it can't be overloaded
            raise SpecificationError("multiple getters defined for property")
        self.r.get = data

    @tag_handler('set',tag_GetSet)
    def handle_set(self,data):
        if self.r.set: join_func(self.r.set,data)
        else: self.r.set = data


def parse_bool(args,prop,default=False):
    val = args.get(prop,None)
    if val is None: return default
    try:
        return {'true':True,'false':False}[val.lower()]
    except LookupError:
        raise ParseError('The value of "{0}" must be either "true" or "false"'.format(prop))


def parse_cint(x):
    if x.startswith('0x') or x.startswith('0X'):
        return int(x[2:],16)
    if x.startswith('0'):
        return int(x,8)
    return int(x)



class AttrLexer(object):
    def __init__(self,data):
        self.data = data

    word = re.compile(r'\w+')
    space = re.compile(r'\s+')

    def __call__(self):
        m = AttrLexer.space.match(self.data)
        if m:
            self.data = self.data[m.end():]

        if not self.data:
            return None

        m = AttrLexer.word.match(self.data)
        if m:
            self.data = self.data[m.end():]
            return m.group()

        m = self.data[0]
        self.data = self.data[1:]
        return m


class tag_Member(tag):
    def __init__(self,args):
        self.r = MemberDef()
        cmember = args['cmember']
        self.r.cmember = tag_Member.parse_cmember(cmember)
        self.r.name = get_valid_py_ident(args.get('name'),cmember)
        self.r.readonly = parse_bool(args,'readonly')

    @staticmethod
    def parse_cmember(x):
        """Parses a C attribute.

        Takes a string in the format:

            identifier ( '.' identifier | '[' integer ']' )*

        and returns a list of strings and numbers where a string corresponds to
        attribute access and a number corresponds to subscript access.
        """
        lex = AttrLexer(x)
        head = lex()
        if not head:
            raise ParseError('cmember cannot be blank')

        seq = [head]
        while True:
            head = lex()

            if head is None:
                return seq

            if head == '.':
                id = lex()
                if not id: break
                seq.append(id)
            elif head == '[':
                num = lex()
                if not num: break
                try:
                    num = parse_cint(num)
                except ValueError:
                    break
                seq.append(num)
                if lex() != ']': break
            else:
                break

        raise ParseError('''cmember must have the following format: "identifier ( '.' identifier | '[' integer ']' )*"''')



def get_ret_semantic(args):
    rs = args.get('return-semantic')
    if rs is not None:
        mapping = {
            'copy' : RET_COPY,
            'managedref' : RET_MANAGED_REF,
            'managedptr' : RET_MANAGED_PTR,
            'unmanagedref' : RET_UNMANAGED_REF,
            'self' : RET_SELF,
            'default' : None}
        try:
            rs = mapping[rs]
        except LookupError:
            raise ParseError('return-semantic (if specified) must be one of the following: {0}'.format(', '.join(mapping.iterkeys())))
    return rs


class tag_Def(tag):
    def __init__(self,args):
        assign = False
        func = args.get('func')
        if func is None:
            func = args.get('assign-to')
            if func is None:
                raise ParseError('One of "func" or "assign-to" is required')
            assign = True
        else:
            if 'assign-to' in args:
                raise ParseError('"func" and "assign-to" cannot be used together')
        self.r = DefDef(get_valid_py_ident(args.get('name'),func))
        arity = args.get('arity')
        if arity:
            def badval():
                raise ParseError('"arity" must be a non-negative integer')

            try:
                arity = int(arity)
            except ValueError:
                badval()
            if arity < 0:
                badval()
        self.r.overloads.append(Overload(
            tag_Def.operator_parse(func),
            get_ret_semantic(args),
            args.get('overload'),
            parse_bool(args,'static'),
            arity,
            assign,
            parse_bool(args,'bridge-virtual',True)))


    op_parse_re = re.compile(r'.*\boperator\b')

    @staticmethod
    def operator_parse(x):
        """Normalize operator function names"""
        m = tag_Def.op_parse_re.match(x)
        if m:
            return m.group(0) + ' ' + ''.join(x[m.end():].split())
        return x

    @tag_handler('doc',tag_Doc)
    def handle_doc(self,data):
        self.r.doc = data


def parse_gc_list(args,name):
    gc = args.get(name)
    return gc and map(str.trim,gc.split(';'))

class tag_Class(tag):
    def __init__(self,args):
        t = args['type']
        self.r = ClassDef(
            get_valid_py_ident(args.get('name'),t),
            t,
            parse_bool(args,'new-initializes'),
            parse_bool(args,'instance-dict',True),
            parse_bool(args,'weakrefs',True),
            parse_bool(args,'use-gc',True),
            parse_gc_list(args,'gc-include'),
            parse_gc_list(args,'gc-ignore'))

    @staticmethod
    def noinit_means_noinit():
        raise SpecificationError("You can't have both <no-init> and <init>")

    @tag_handler('init',tag_Init)
    def handle_init(self,data):
        if self.r.constructor is NoInit:
            tag_Class.noinit_means_noinit()

        if self.r.constructor: join_func(self.r.constructor,data)
        else: self.r.constructor = data

    @tag_handler('doc',tag_Doc)
    def handle_doc(self,data):
        self.r.doc = data

    @tag_handler('property',tag_Property)
    def handle_property(self,data):
        self.r.properties.append(data)

    @tag_handler('attr',tag_Member)
    def handle_attr(self,data):
        self.r.vars.append(data)

    @tag_handler('def',tag_Def)
    def handle_def(self,data):
        add_func(self.r.methods,data)

    @tag_handler('no-init',tag)
    def handle_noinit(self,data):
        if self.r.constructor:
            tag_Class.noinit_means_noinit()
        self.r.constructor = NoInit


class tag_Var(tag):
    def __init__(self,args):
        val = args['value'].strip()

        ref = args.get('ref')
        if ref is not None:
            mapping = {
                'true' : RET_UNMANAGED_REF,
                'false' : RET_COPY,
                'copy' : RET_COPY,
                'managedptr' : RET_MANAGED_PTR,
                'unmanagedref' : RET_UNMANAGED_REF}
            try:
                ref = mapping[ref]
            except LookupError:
                raise ParseError('ref (if specified) must be one of the following: {0}'.format(', '.join(mapping.iterkeys())))
        else:
            ref = RET_UNMANAGED_REF

        self.r = VarDef(val,get_valid_py_ident(args.get('name'),val),ref)


class tag_GCHandler(tag):
    def __init__(self,args):
        self.type = args['type']
        self.traverse = None
        self.clear = None

    @tag_handler('traverse',tag_ToFromPyObject)
    def handle_traverse(self,data):
        self.traverse = data

    @tag_handler('clear',tag_ToFromPyObject)
    def handle_clear(self,data):
        self.clear = data

    def end(self):
        if self.traverse is None:
            raise SpecificationError('<traverse> must be defined for <gc-handler>')
        return self.type,self.traverse,self.clear


class tag_Module(tag):
    def __init__(self,args):
        self.r = ModuleDef(args["name"],stripsplit(args["include"]))

    @tag_handler('class',tag_Class)
    def handle_class(self,data):
        self.r.classes.append(data)

    @tag_handler('def',tag_Def)
    def handle_def(self,data):
        add_func(self.r.functions,data)

    @tag_handler('doc',tag_Doc)
    def handle_doc(self,data):
        self.r.doc = data

    @tag_handler('to-pyobject',tag_ToFromPyObjectWithType)
    def handle_to(self,data):
        self.r.topy.append(data)

    @tag_handler('from-pyobject',tag_ToFromPyObjectWithType)
    def handle_from(self,data):
        self.r.frompy.append(data)

    @tag_handler('smart-ptr',tag_SmartPtr)
    def handle_smartptr(self,data):
        self.r.smartptrs.append(data)

    @tag_handler('var',tag_Var)
    def handle_var(self,data):
        if data.name in self.r.vars:
            raise SpecificationError('var "{0}" is defined more than once'.format(data.name))
        self.r.vars[data.name] = data

    @tag_handler('gc-handler',tag_GCHandler)
    def handle_gchandler(self,data):
        self.r.gchandlers.append(data)



def getspec(path):
    return parse(path,'module',tag_Module)
