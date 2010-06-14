
import re
import itertools
import os.path
import copy
import sys
import textwrap
import functools

from xmlparse import *
import err
import gccxml
import espectmpl as tmpl


TEST_NS = "___gccxml_types_test_ns___"
UNINITIALIZED_ERR_TYPE = "PyExc_RuntimeError"

RET_COPY = 1
RET_MANAGED_REF = 2
RET_SELF = 3

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

def _namespace_find(self,x,test):
    parts = x.split('::',1)
    levels = LevelBaseTraverser(self) if isinstance(self,gccxml.CPPClass) else [[self]]
    for l in levels:
        matches = [real_type(m) for m in itertools.chain.from_iterable(i.members for i in l) if hasattr(m,"canon_name") and m.canon_name == parts[0] and test(m)]

        if matches:
            if len(parts) == 2:
                if not isinstance(matches[0],(gccxml.CPPClass,gccxml.CPPNamespace)):
                    raise SpecificationError('"{0}" is not a namespace, struct or class'.format(parts[0]))

                assert len(matches) == 1
                return _namespace_find(matches[0],parts[1])

            return matches
    return []

def namespace_find(self,x,test=None):
    if test is None: test = lambda x: True
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

    raise SpecificationError('could not find "{0}"'.format(x))

gccxml.CPPClass.find = namespace_find
gccxml.CPPNamespace.find = namespace_find



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


def mandatory_args(x):
    return len(list(itertools.takewhile(lambda a: a.default is None, x.args)))


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


class Overload:
    def __init__(self,func=None,retsemantic=None,args=None,static=False,arity=None):
        self.func = func
        self.retsemantic = retsemantic
        self.args = args
        self.static = static
        self.arity = arity

class DefDef:
    def __init__(self,name = None,doc = None):
        self.name = name
        self.doc = doc
        self.overloads = []

class CallCode(object):
    """C++ code representing a function call with optional predefined argument values."""
    def __init__(self,code,binds=None):
        self.code = code
        self.binds = binds or []

    def output(self,args,ind):
        args = list(args)
        for i,val in self.binds:
            args.insert(i,val)
        return self.code.format(','.join('\n'+ind+a for a in args))

class BindableArg:
    def __init__(self,arg,val=None):
        self.arg = arg
        self.val = val

class TypedOverload:
    def __init__(self,func,overload):
        self.func = func
        self.retsemantic = overload.retsemantic
        self.argbinds = [BindableArg(a) for a in func.args]
        self.explicit_static = overload.static

    def bind(self,index,val):
        [a for a in self.argbinds if a.val is None][index].val = val

    @property
    def args(self):
        return [a.arg for a in self.argbinds if a.val is None]

    def can_accept(self,args):
        if not (mandatory_args(self) <= args <= len(self.args)):
            raise SpecificationError(
                '"{0}" must take {1} argument(s)'.format(
                    self.func.canon_name,
                    args + sum(1 for a in self.argbinds if a.val is not None)))

    @property
    def static(self):
        return self.explicit_static or isinstance(self.func,gccxml.CPPFunction) or (isinstance(self.func,gccxml.CPPMethod) and self.func.static)

class TypedDefDef(object):
    def __init__(self,scope,defdef):
        self.name = defdef.name
        self.doc = defdef.doc
        self.overloads = []

        for ov in defdef.overloads:
            extratest = None
            if ov.arity is not None:
                extratest = lambda x: mandatory_args(x) <= ov.arity <= len(x.args)
            cf = scope.find(ov.func,extratest)

            # if the first one is a function, they all should be functions
            if not isinstance(cf[0],(gccxml.CPPFunction,gccxml.CPPMethod)):
                raise SpecificationError('"{0}" is not a function or method'.format(cf[0]))
            assert all(isinstance(f,(gccxml.CPPFunction,gccxml.CPPMethod)) for f in cf)

            self.overloads.extend(TypedOverload(f,ov) for f in cf if (not ov.args) or same_args(ov.args,f.args))

    def call_code_base(self,ov):
        return ov.func.canon_name

    def topy(self,conv,t,retsemantic):
        return conv.topy(t,retsemantic)

    def call_code(self,conv,ov):
        code = self.call_code_base(ov) + '({0})'
        return CallCode(code + '; Py_RETURN_NONE;' if ov.func.returns == conv.void else
            'return {0};'.format(self.topy(conv,ov.func.returns,ov.retsemantic).format(code)),
            [(i,argbind.val) for i,argbind in enumerate(ov.argbinds) if argbind.val])

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
            prolog = prolog,
            epilog = '',
            name = funcnameprefix + self.name,
            args = selfvar + funcargs,
            code = code,
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
        return x.full_name()

    return 'base.' + x.canon_name


class TypedMethodDef(TypedDefDef):
    selfvar = 'reinterpret_cast<PyObject*>(self)'

    def __init__(self,classdef,defdef):
        super(TypedMethodDef,self).__init__(classdef.type,defdef)
        self.classdef = classdef

        for ov in self.overloads:
            if isinstance(ov.func,gccxml.CPPFunction) and not ov.explicit_static:
                if len(ov.func.args) == 0 or strip_refptr(ov.func.args[0].type) != self.classdef.type:
                    self.odd_function(ov)
                else:
                    ov.bind(0,'&base' if isinstance(ov.func.args[0].type,gccxml.CPPPointerType) else 'base')

    def static(self):
        return all(ov.func.static for ov in self.overloads)

    def call_code_base(self,ov):
        if isinstance(ov.func,gccxml.CPPMethod):
            return base_prefix(ov.func)

        return super(TypedMethodDef,self).call_code_base(ov)

    def call_code(self,conv,ov):
        if ov.retsemantic == RET_SELF:
            return CallCode(self.call_code_base(ov) + '({{0}}); Py_INCREF({0}); return {0};'.format(self.selfvar))
        return super(TypedMethodDef,self).call_code(conv,ov)

    def odd_function(self,ov):
        raise SpecificationError('The first parameter of "{0}" should be of type "{1}" or be a reference or pointer to it.'.format(ov.func.name,self.classdef.type.type_str()))

    def topy(self,conv,t,retsemantic):
        return conv.topy(t,retsemantic,'self')

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
    def __init__(self,classdef,defdef,argtype,rettype = SF_RET_OBJ,defretsemantic = None):
        super(SpecialMethod,self).__init__(classdef,defdef)
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
                if not ov.func.returns in conv.integers:
                    raise SpecificationError('"{0}" must return an integer type'.format(ov.func.canon_name))
            elif self.rettype == SF_RET_INT_BOOL:
                if not (ov.func.returns in conv.integers or ov.func.returns == conv.bool):
                    raise SpecificationError('"{0}" must return an integer or bool type'.format(ov.func.canon_name))

    def call_code_cast(self,conv,ov,t):
        return CallCode('return static_cast<{0}>({1}({{0}}));'.format(t,self.call_code_base(ov)))

    def call_code(self,conv,ov):
        if self.rettype == SF_RET_INT or self.rettype == SF_RET_INT_BOOL:
            return self.call_code_cast(conv,ov,'int')

        if self.rettype == SF_RET_LONG:
            return self.call_code_cast(conv,ov,'long')

        if self.rettype == SF_RET_SSIZE:
            return self.call_code_cast(conv,ov,'Py_ssize_t')

        if self.rettype == SF_RET_INT_VOID:
            return CallCode(self.call_code_base(ov) + '({0}); return 0;')

        assert self.rettype == SF_RET_OBJ
        return super(SpecialMethod,self).call_code(conv,ov)

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
            prolog = self.classdef.method_prolog(),
            epilog = '',
            code = code,
            errval = errval)


class FOpMethod(SpecialMethod):
    selfvar = 'a'


class BinaryROpMethod(SpecialMethod):
    selfvar = 'b'

    def __init__(self,classdef,defdef):
        super(BinaryROpMethod,self).__init__(classdef,defdef,SF_ONE_ARG)

    def odd_function(self,ov):
        if not(len(ov.func.args) >= 2 and strip_refptr(ov.func.args[1].type) == self.classdef.type):
            raise SpecificationError('The first or second parameter of "{0}" should be of type "{1}" or be a reference or pointer to it.'.format(ov.func.name,self.classdef.type.typestr()))

        ov.bind(1,'&base' if isinstance(ov.func.args[1].type,gccxml.CPPPointerType) else 'base')


class InitDef:
    def __init__(self):
        self.doc = None
        self.overloads = []


class PropertyDef:
    def __init__(self,name,get=None,set=None):
        self.name = name
        self.get = get
        self.set = set
        self.doc = None

class TypedPropertyDef:
    def __init__(self,classdef,propdef):
        self.name = propdef.name
        self.doc = propdef.doc
        self.get = SpecialMethod(classdef,propdef.get,SF_NO_ARGS,SF_RET_OBJ)
        self.set = SpecialMethod(classdef,propdef.set,SF_ONE_ARG,SF_RET_INT_VOID)

    def output(self,conv):
        r = ''
        if self.get:
            r = tmpl.property_get.render(
                cname = self.get.classdef.name,
                prolog = '' if self.get.static() else self.get.classdef.method_prolog(),
                name = self.name,
                checkinit = True,
                code = self.get.function_call_0arg(conv))

        if self.set:
            r += tmpl.property_set.render(
                cname = self.set.classdef.name,
                prolog = '' if self.set.static() else self.set.classdef.method_prolog(),
                name = self.name,
                checkinit = True,
                code = self.set.function_call_1arg(conv))

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


class TypedMemberDef:
    def __init__(self,classdef,memdef):
        self.classdef = classdef
        self.name = memdef.name
        self.doc = memdef.doc
        self.readonly = memdef.readonly
        self.cmember = classdef.type.find(memdef.cmember)[0]
        if not isinstance(self.cmember,gccxml.CPPField):
            raise SpecificationError('"{0}" is not a member variable'.format(memdef.cmember))

    def getter_type(self,conv):
        return self.cmember.type if conv.member_macro(self.cmember.type) else gccxml.CPPReferenceType(self.cmember.type)

    def output(self,conv):
        r = ''
        if self.classdef.has_multi_inherit_subclass() or not conv.member_macro(self.cmember.type):
            r = tmpl.property_get.render(
                cname = self.classdef.name,
                prolog = '' if self.cmember.static else self.classdef.method_prolog(),
                name = self.name,
                checkinit = True,
                code = '        return {0};'.format(
                    conv.topy(self.getter_type(conv),RET_MANAGED_REF,'self').format(base_prefix(self.cmember))))

            if not self.readonly:
                r += tmpl.property_set.render(
                    cname = self.classdef.name,
                    prolog = '' if self.cmember.static else self.classdef.method_prolog(),
                    name = self.name,
                    checkinit = True,
                    code = '        {0} = {1};\n        return 0;'.format(base_prefix(self.cmember),conv.frompy(self.cmember.type)[0].format('arg')))
        return r

    def really_a_property(self,conv):
        return self.classdef.has_multi_inherit_subclass() or not conv.member_macro(self.cmember.type)

    def table_entry(self,conv):
        mm = conv.member_macro(self.cmember.type)
        if self.classdef.has_multi_inherit_subclass() or not mm:
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
            mname = self.cmember.name,
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


class ClassDef:
    def __init__(self):
        self.constructor = None
        self.methods = MethodDict()
        self.properties = []
        self.vars = []
        self.doc = None


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


class TypedClassDef:
    def __init__(self,scope,classdef):
        self.name = classdef.name
        self.type = scope.find(classdef.type)[0]
        if not isinstance(self.type,gccxml.CPPClass):
            raise SpecificationError('"{0}" is not a struct/class type'.format(classdef.type))

        self.constructor = classdef.constructor

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

        # TypedOverload.bind will be used to cover the Py_ssize argument
        SSizeArg = NoArgs
        SSizeObjArgs = OneArg
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
            ('__mapping__setitem__', TwoArgs), # tp_as_mapping.mp_ass_subscript
            ('__sequence__setitem__', SSizeObjArgs) # tp_as_sequence.sq_ass_item
        ):
            m = classdef.methods.data.pop(key,None)
            if m: self.special_methods[key] = mtype(self,m)

        self.methods = [TypedMethodDef(self,dd) for dd in classdef.methods.data.itervalues()]

        self.properties = [TypedPropertyDef(self,pd) for pd in classdef.properties]
        self.vars = [TypedMemberDef(self,mdef) for mdef in classdef.vars]
        self.doc = classdef.doc

        self.bases = []
        self.derived = []
        self.features = ObjFeatures()

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
            features = self.features)

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
                    prolog = self.method_prolog(),
                    epilog = tmpl.ret_notimplemented,
                    code = f.function_call_1arg_fallthrough(out.conv),
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
                prolog = self.method_prolog(),
                epilog = tmpl.ret_notimplemented,
                code = splitdefdef23code(f,out.conv,['arg1','arg2']),
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

        for n in ('__sequence_len__','__concat__','__iconcat__','__contains__'):
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
                    args = 'obj_{0} *self,Py_ssize_t *count'.format(self.name),
                    prolog = self.method_prolog(),
                    epilog = '',
                    code = f.function_call_0arg(out.conv),
                    errval = '0')

        f = self.special_methods.get('__sequence_getitem__')
        if f:
            have = True
            bindpyssize(out.conv,f,'index')
            print >> out.cpp, tmpl.function.format(
                rettype = 'PyObject *',
                name = 'obj_{0}___sequence_getitem__'.format(self.name),
                args = 'obj_{0} *self,Py_ssize_t *index'.format(self.name),
                prolog = self.method_prolog(),
                epilog = '',
                code = f.function_call_0arg(out.conv),
                errval = '0')

        f = self.special_methods.get('__sequence_setitem__')
        if f:
            have = True
            bindpyssize(out.conv,f,'index')
            print >> out.cpp, tmpl.function.format(
                rettype = 'int',
                name = 'obj_{0}___sequence_setitem__'.format(self.name),
                args = 'obj_{0} *self,Py_ssize_t *index,PyObject *arg'.format(self.name),
                prolog = self.method_prolog(),
                epilog = '',
                code = f.function_call_1arg(out.conv),
                errval = '-1')

        if have:
            print >> out.cpp, tmpl.sequence_methods.render(
                name = self.name,
                specialmethods = self.special_methods)

        return have

    def mapping(self,out):
        have = False

        for fn in ('__mapping_len__','__mapping_getitem__','__mapping_setitem__'):
            if self.output_special(fn,out): have = True

        if have:
            print >> out.cpp, tmpl.mapping_methods.render(
                name = self.name,
                specialmethods = self.special_methods)

        return have

    def method_prolog(self,var='reinterpret_cast<PyObject*>(self)',ind=Tab(1)):
        return '{0}{1} &base = {2};\n'.format(
            ind,
            self.type.canon_name,
            ('get_base_{0}({1},false)' if self.has_multi_inherit_subclass() else 'cast_base_{0}({1})').format(self.name,var))

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

        richcompare = self.rich_compare(out)
        number = self.number(out)
        mapping = self.mapping(out)
        sequence = self.sequence(out)

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


        func = CallCode('new(addr) {0}({{0}}); return 0;'.format(self.type.canon_name))
        if self.constructor:
            if self.constructor.overloads[0].args is None:
                # no overload specified means use all constructors

                assert len(self.constructor.overloads) == 1
                cons = [(func,con.args) for con in self.type.members if isinstance(con,gccxml.CPPConstructor)]
            else:
                cons = [(func,self.type.getConstructor(ov.args).args) for args in self.constructor.overloads]
        else:
            cons = [(func,self.type.getConstructor().args)]

        cons = out.conv.function_call(cons,'-1',True)

        print >> out.cpp, tmpl.classtypedef.render(
            dynamic = self.dynamic,
            name = self.name,
            type = self.type.canon_name,
            features = self.features,
            initdestruct = initdestruct,
            initcode = cons,
            module = module,
            destructref = destructref,
            doc = self.doc,
            getsetref = getsetref,
            membersref = membersref,
            methodsref = methodsref,
            richcompare = richcompare,
            number = number,
            mapping = mapping,
            sequence = sequence,
            bases = bases,
            specialmethods = self.special_methods),



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





class Conversion:
    def __init__(self,tns):
        # get the types specified by the typedefs
        for x in ("bool","sint","uint","sshort","ushort","slong","ulong",
                  "float","double","long_double","size_t","py_ssize_t","schar",
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
            gccxml.CPPBasicType('Py_ssize_t') : 'T_PYSSIZET'
        }

        self.integers = set((self.sint,self.uint,self.sshort,self.ushort,
            self.slong,self.ulong,self.size_t,self.py_ssize_t,self.schar,
            self.uchar,self.char))

        if self.slonglong:
            self.__topy[self.slonglong] = 'PyLong_FromLongLong({0})'
            self.__topy[self.ulonglong] = 'PyLong_FromUnsignedLongLong({0})'

            self.basic_types[TYPE_LONG].add(self.slonglong)
            self.basic_types[TYPE_LONG].add(self.ulonglong)

            self.__frompy[self.slonglong] = (False,'PyToLongLong({0})')
            self.__frompy[self.ulonglong] = (False,'PyToULongLong({0})')

            self.from_py_ssize_t[self.slonglong] = '{0}'

            # to_ulong is used since 'Py_ssize_t' isn't going to be larger than 'long' anyway
            self.from_py_ssize_t[self.ulonglong] = 'py_ssize_t_to_ulong({0})'

            self.__pymember[self.slonglong] = 'T_LONGLONG'
            self.__pymember[self.ulonglong] = 'T_ULONGLONG'

            self.integers.add(self.slonglong)
            self.integers.add(self.ulonglong)

        self.cppclasstopy = {}

    def __topy_pointee(self,x):
        return self.__topy.get(strip_cvq(x.type))

    def topy(self,t,retsemantic = None,container = None):
        r = self.__topy.get(t)
        if r: return r

        if isinstance(t,(gccxml.CPPPointerType,gccxml.CPPReferenceType)):
            if retsemantic == RET_COPY:
                if isinstance(t,gccxml.CPPReferenceType):
                    r = self.__topy_pointee(t)
                    if r: return r
                else:
                    r = self.__topy_pointee(t)
                    if r: return r.format('*({0})')
            elif retsemantic == RET_MANAGED_REF:
                classdef = self.cppclasstopy.get(strip_cvq(t.type))
                if classdef:
                    return 'reinterpret_cast<PyObject*>(new ref_{0}({2},reinterpret_cast<PyObject*>({1})))'.format(
                        classdef.name,
                        container,
                        '{0}' if isinstance(t,gccxml.CPPReferenceType) else '*({0})')

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
        return self.__pymember.get(t)


    def check_and_cast(self,t):
        cdef = self.cppclasstopy[strip_refptr(t)]
        check ='PyObject_TypeCheck({{0}},get_obj_{0}Type())'.format(cdef.name)
        cast = '{0}reinterpret_cast<obj_{1}*>({{0}})->base'.format('&' if isinstance(t,gccxml.CPPPointerType) else '',cdef.name)
        return check,cast

    def arg_parser(self,args,use_kwds = True,indent = Tab(2)):
        # even if we are not taking any arguments, get_arg::finish should still be called (to report an error if arguments were received)

        prep = indent.line('get_arg ga(args,{1});'.format(indent,'kwds' if use_kwds else '0'))

        if any(a.default for a in args):
            prep += indent.line('PyObject *temp;')

        for i,a in enumerate(args):
            frompy, frompytype = self.frompy(a.type)
            var = frompytype.typestr("_{0}".format(i))
            name = '"{0}"'.format(a.name) if a.name and use_kwds else '0'
            if a.default:
                prep += '{0}temp = ga({1},false);\n{0}{2} = temp ? {3} : {4};\n'.format(indent,name,var,frompy.format('temp'),a.defult)
            else:
                prep += indent.line('{1} = {2};'.format(indent,var,frompy.format('ga({0},true)'.format(name))))

        prep += indent.line('ga.finished();')

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


def methods_that_return(c):
    return itertools.chain(((m.name,m) for m in c.methods),((p.name,p.get) for p in c.properties if p.get))


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
            conv.add_conv(c.type,'reinterpret_cast<PyObject*>(new obj_{0}({{0}}))'.format(c.name),(True,'get_base_{0}({{0}})'.format(c.name)))
            conv.cppclasstopy[c.type] = c

        for c in classes.itervalues():
            c.findbases(classes)


        # find all methods and functions that return objects that require special storage
        for c in classes.itervalues():
            for name,m in methods_that_return(c):
                for ov in m.overloads:
                    if ov.retsemantic == RET_MANAGED_REF and isinstance(ov.func.returns,(gccxml.CPPReferenceType,gccxml.CPPPointerType)):
                        t = strip_cvq(ov.func.returns.type)
                        retcdef = classes.get(t)
                        if not retcdef:
                            raise SpecificationError('return type of "{0}" is not an exposed type'.format(name))
                        retcdef.features.managed_ref = True

            for v in c.vars:
                if v.really_a_property(conv):
                    gt = v.getter_type(conv)
                    if isinstance(gt,gccxml.CPPReferenceType):
                        cdef = classes.get(gt.type)
                        if not cdef:
                            raise SpecificationError('Attribute "{0}" does not refer to an exposed type'.format(v.name))
                        cdef.features.managed_ref = True


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
            if c.constructor:
                for x in c.constructor.overloads:
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

class tag_Class(tag):
    def __init__(self,args):
        self.r = ClassDef()
        self.r.type = args["type"]
        self.r.name = get_valid_py_ident(args.get("name"),self.r.type)

    def child(self,name,data):
        if name == 'init':
            if self.r.constructor: join_func(self.r.constructor,data)
            else: self.r.constructor = data
        elif name == "doc":
            self.r.doc = data
        elif name == "property":
            self.r.properties.append(data)
        elif name == 'attr':
            self.r.vars.append(data)
        elif name == 'def':
            add_func(self.r.methods,data)


class tag_Init(tag):
    def __init__(self,args):
        self.r = InitDef()
        self.r.overloads.append(Overload(args=args.get("overload")))

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
    if not x: return None
    r = DefDef()
    r.overloads.append(Overload(x))
    return r

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

    def child(self,name,data):
        if name == "doc":
            self.r.doc = data
        elif name == "get":
            if self.r.get:
                # only one get is allowed since it can't overloaded
                raise SpecificationError("multiple getters defined for property")
            self.r.get = data
        elif name == "set":
            if self.r.set: join_func(self.r.set,data)
            else: self.r.set = data

def parse_bool(args,prop):
    val = args.get(prop,False)
    if val is False: return False
    try:
        return {'true':True,'false':False}[val.lower()]
    except LookupError:
        raise ParseError('The value of "{0}" must be either "true" or "false"'.format(prop))

class tag_Member(tag):
    def __init__(self,args):
        self.r = MemberDef()
        self.r.cmember = args['cmember']
        self.r.name = get_valid_py_ident(args.get('name'),self.r.cmember)
        self.r.readonly = parse_bool(args,'readonly')


def get_ret_semantic(args):
    rs = args.get('return-semantic')
    if rs is not None:
        mapping = {
            'copy' : RET_COPY,
            'managedref' : RET_MANAGED_REF,
            'self' : RET_SELF,
            'default' : None}
        try:
            rs = mapping[rs]
        except LookupError:
            raise ParseError('return-semantic (if specified) must be one of the following: {0}'.format(', '.join(mapping.keys())))
    return rs

class tag_GetSet(tag):
    def __init__(self,args):
        self.r = DefDef()
        self.r.overloads.append(Overload(
            tag_Def.operator_parse(args['func']),
            get_ret_semantic(args),
            args.get('overload')))

class tag_Def(tag):
    def __init__(self,args):
        func = args['func']
        self.r = DefDef(get_valid_py_ident(args.get('name'),func))
        arity = args.get('arity')
        if arity:
            def badval():
                raise ParseError('"arity" must be a positive integer')

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
            arity))


    op_parse_re = re.compile(r'.*\boperator\b')

    @staticmethod
    def operator_parse(x):
        """Normalize operator function names"""
        m = tag_Def.op_parse_re.match(x)
        if m:
            return m.group(0) + ' ' + ''.join(x[m.end():].split())
        return x

tagdefs = {
    "class" : tag_Class,
    "init" : tag_Init,
    "module" : tag_Module,
    "property" : tag_Property,
    "doc" : tag_Doc,
    "attr" : tag_Member,
    'get' : tag_GetSet,
    'set' : tag_GetSet,
    'def' : tag_Def
}

def getspec(path):
    return parse(path,tagdefs)
