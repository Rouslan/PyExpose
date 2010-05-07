
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

GETTER = 1
SETTER = 2


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

def namespace_find(self,x):
    parts = x.split('::',1)
    levels = LevelBaseTraverser(self) if isinstance(self,gccxml.CPPClass) else [[self]]
    for l in levels:
        matches = [m for m in itertools.chain.from_iterable(i.members for i in l) if hasattr(m,"name") and m.name == parts[0]]

        if len(matches) > 1:
            raise SpecificationError('"{0}" is ambiguous'.format(parts[0]))

        if matches:
            if len(parts) == 2:
                if not isinstance(matches[0],Namespace):
                    raise SpecificationError('"{0}" is not a namespace, struct or class type'.format(parts[0]))
                return matches[0].find(parts[1])

            return matches[0]

    raise SpecificationError('could not find "{0}" in this scope'.format(parts[0]))
gccxml.CPPClass.find = namespace_find
gccxml.CPPNamespace.find = namespace_find



def quote_c(x):
    # python's non-unicode string syntax appears to be the same as C's
    return '"'+x.encode('utf_8').encode('string_escape')+'"'

def mandatory_args(x):
    return len(list(itertools.takewhile(lambda a: a.default is None, x.args)))

def internconstructor(classname,c):
    return tmpl.internconstruct.format(
        name = classname,
        checkinit = True,
        args = ','.join('{0!s} _{1}'.format(a,i) for i,a in enumerate(c.args)),
        argvals = ','.join('_{0}'.format(i) for i in xrange(len(c.args))))

def varargs(x):
    return x.args and x.args[-1] is gccxml.cppellipsis



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
        raise SpecificationError('"{0}" cannot be used as a {1} because it is static'.format(name,'setter' if which == 2 else 'getter'))

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

            r = tmpl.property_get.format(
                cname = classdef.name,
                ctype = classdef.type,
                name = self.name,
                checkinit = True,
                code = conv.topy(f.returns,self.get.retsemantic).format("self->base.{0}()".format(self.get.func)))

        if self.set:
            f = classint.find(self.set.func)
            check_getsetint(self.get.func,f,SETTER)

            if mandatory_args(f) != 1:
                raise SpecificationError('"{0}" must take exactly one argument'.format(self.set.func))

            r += tmpl.property_set.format(
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
            doc = quote_c(self.doc) if self.doc else "0")

class MemberDef:
    doc = None

    def table_entry(self,classdef,classint,conv):
        m = classint.find(self.cmember)
        if not isinstance(m,gccxml.CPPField):
            raise SpecificationError('"{0}" is not a member variable'.format(self.cmember))

        # TODO: offsetof is not relaiable for non-POD types (with g++, it will fail for classes with diamond virtual inheritance). A better solution is needed.
        return '{{const_cast<char*>("{name}"),{type},offsetof(obj_{classdefname},base) + offsetof({classname},{mname}),{flags},{doc}}}'.format(
            name = self.name,
            type = conv.member_macro(m.type),
            classdefname = classdef.name,
            classname = classint.name,
            mname = self.cmember,
            flags = 'READONLY' if self.readonly else '0',
            doc = 'const_cast<char*>({0})'.format(quote_c(self.doc)) if self.doc else '0')

class GetSetDef:
    def __init__(self,func,retsemantic = None):
        self.func = func
        self.retsemantic = retsemantic

class DefDef:
    doc = None

class ClassDef:
    def __init__(self):
        self.constructors = []
        self.methods = []
        self.properties = []
        self.vars = []
        self.doc = None
        self.bases = []

    def findbases(self,cppint,classint,classdefs):
        for b in classint.bases:
            cd = classdefs.get(b.type)
            if cd:
                self.bases.append(cd)

    def basecount(self):
        return sum(1 + b.basecount() for b in self.bases)

    @property
    def dynamic(self):
        return len(self.bases) > 1

    @property
    def type_cptr(self):
        r = 'obj_{name}Type'
        if not self.dynamic:
            r = '&' + r
        return r

    def method(self,m,classint,conv):
        cm = classint.find(m.func)

        if not isinstance(cm,gccxml.CPPMethod):
            raise SpecificationError('"{0}" is not a method'.format(m.func))

        if len(cm.args) == 0:
            type = 'METH_NOARGS'
            methargs = ',PyObject *'
            argcode = cppcode('')
        elif len(cm.args) == 1 and not cm.args[0].default:
            type == 'METH_O'
            methargs = ',PyObject *arg'
            argcode = cppcode(conv.frompy(cm.args[0])[0])
        elif any(a.name for a in cm.args): # is there a named argument?
            type == 'METH_VARARGS|METH_KEYWORDS'
            methargs = ',PyObject *args,PyObject *kwds'
            argcode = conv.arg_parser(cm.args)
        else:
            type == 'METH_VARAGS'
            methargs = ',PyObject *args'
            argcode = conv.arg_parser(cm.args,False)

        code = 'self->base.'
        if cm.static:
            type += '|METH_STATIC'
            code = '{0}::'.format(self.type)

        code = '{0}{1}({2})'.format(code,m.func,argcode.val)

        if cm.returns == conv.void:
            code += ';\n        Py_RETURN_NONE'
        else:
            code = 'return {0};'.format(conv.topy(cm.returns,m.retsemantic).format(code))

        if argcode.prep:
            code = '{0};\n        {1};'.format(argcode.prep,code)

        funcbody = tmpl.method.format(
            name = m.name,
            cname = self.name,
            args = methargs,
            checkinit = not cm.static,
            code = code)

        tableentry = '{{"{name}",reinterpret_cast<PyCFunction>(obj_{cname}_method_{name}),{type},{doc}}}'.format(
            cname = self.name,
            name = m.name,
            type = type,
            doc = quote_c(m.doc) if m.doc else '0')

        return tableentry,funcbody

    def output(self,out,module,cppint):
        c = cppint.find(self.type)
        if not isinstance(c,gccxml.CPPClass):
            raise SpecificationError('"{0}" is not a struct/class type'.format(self.type))


        print >> out.h, tmpl.classdef_start.format(
            name = self.name,
            type = self.type),

        for m in c.members:
            if isinstance(m,gccxml.CPPConstructor) and not varargs(m):
                print >> out.h, internconstructor(self.name,m),

        print >> out.h, tmpl.classdef_end,


        destructref = '0'
        initdestruct = ''
        d = c.getDestructor()
        if d:
            print >> out.cpp, tmpl.destruct.format(name = self.name, dname = d.name),
            destructref = 'reinterpret_cast<destructor>(&obj_{0}_dealloc)'.format(self.name)
            initdestruct = '    if(self->initialized) self->base.~{0}();'.format(d.name)


        getsetref = None
        if self.properties:
            for p in self.properties:
                print >> out.cpp, p.output(self,c,cppint,out.conv),

            print >> out.cpp,  tmpl.getset_table.format(
                name = self.name,
                items = ',\n    '.join(p.table_entry(self) for p in self.properties)),

            getsetref = 'obj_{0}_getset'.format(self.name)

        membersref = None
        if self.vars:
            print >> out.cpp, tmpl.member_table.format(
                name = self.name,
                items = ',\n    '.join(v.table_entry(self,c,out.conv) for v in self.vars)),
            membersref = 'obj_{0}_members'.format(self.name)


        methodsref = None
        if self.methods:
            tentries,bodies = zip(*[self.method(m,c,out.conv) for m in self.methods])
            for b in bodies:
                print >> out.cpp, b,

            print >> out.cpp, tmpl.method_table.format(
                name = self.name,
                items = ',\n    '.join(tentries)),

            methodsref = 'obj_{0}_methods'.format(self.name)


        func = 'new(&self->base) ' + self.type
        if self.constructors:
            cons = [(func,c.getConstructor(con.overload).args) for con in self.constructors]
        else:
            cons = [(func,c.getConstructor().args)]

        cons = out.conv.function_call(cons,True)

        print >> out.cpp, tmpl.classinit.format(
            name = self.name,
            initdestruct = initdestruct,
            initcode = self.cons),

        if self.dynamic:
            print >> out.cpp, tmpl.class_dynamic_typedef.format(
                name = self.name,
                module = module,
                destructref = destructref,
                doc = self.doc and quote_c(self.doc),
                getsetref = getsetref,
                membersref = membersref,
                methodsref = methodsref,
                baselen = len(self.bases),
                baseassign = '\n    '.join('PyTuple_SET_ITEM(bases,{0},{1});'.format(*x) for x in enumerate(self.bases))),
        else:
            print >> out.cpp, tmpl.classtypedef.format(
                name = self.name,
                module = module,
                destructref = destructref or '0',
                doc = quote_c(self.doc) if self.doc else '0',
                getsetref = getsetref or '0',
                membersref = membersref or '0',
                methodsref = methodsref or '0'),


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
            return val + self.__str__() + val
        return Tab(self.amount + val)

    def __sub__(self,val):
        return Tab(self.amount - val)
    __rsub__ = __sub__



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
        """Sort self.objects on this instance and all child instances so that no CPPClass is preceded by its base class.

        When comparing types, if S inherits from B, and our type T matches S, then T will always match B, so S must be tested first, since
        the tests will stop after finding the first viable match."""
        self.objects.sort(key = (lambda x: base_count(strip_refptr(x[0]))),reverse = True)
        for n in self.child_nodes(): n.sort_objects()

    def basic_and_objects_code(self,conv,argconv,skipsize,ind,exactlenchecked = False):
        r = ''

        # check for general classes
        if self.objects:
            for t,branch in self.objects:
                check,cast = conv.check_and_cast(t)
                r += '{0}if({1}) {{\n{2}{0}}}\n'.format(
                    ind,
                    check.format('PyTuple_GET_ITEM(args,{0})'.format(len(argconv))),
                    branch.get_code(conv,argconv + [cast],skipsize,ind + 1,exactlenchecked))


        # check for numeric types
        nums = 0
        if self.basic[TYPE_FLOAT]: nums |= CHECK_FLOAT
        if self.basic[TYPE_INT]: nums |= CHECK_INT
        if self.basic[TYPE_LONG]: nums |= CHECK_LONG
        if nums:
            for c,t in zip(coercion[nums],[TYPE_FLOAT,TYPE_INT,TYPE_LONG]):
                if c:
                    r += '{0}if({2}(PyTuple_GET_ITEM(args,{1}))) {{\n{3}{0}}}\n'.format(
                        ind,
                        len(argconv),
                        c,
                        self.basic[t].get_code(conv,argconv + [None],skipsize,ind + 1,exactlenchecked))


        # check for string types
        if self.basic[TYPE_UNICODE]:
            r += '{0}if(PyUnicode_Check(PyTuple_GET_ITEM(args,{1})){2}) {{\n{3}{0}}}\n'.format(
                ind,
                len(argconv),
                '' if self.basic[TYPE_STR] else ' && PyString_Check(o)',
                self.basic[TYPE_UNICODE].get_code(conv,argconv + [None],skipsize,ind + 1,exactlenchecked))

        if self.basic[TYPE_STR]:
            r += '{0}if(PyString_Check(PyTuple_GET_ITEM(args,{1}))) {{\n{2}{0}}}\n'.format(
                ind,
                len(argconv),
                self.basic[TYPE_STR].get_code(conv,argconv + [None],skipsize,ind + 1,exactlenchecked))

        return r

    def call_code(self,conv,argconv,ind):
        return '{0}{1}(\n{0}    {2});\n{0}goto end;\n'.format(
            ind,
            self.call[0],
            ',\n{0}    '.format(ind).join(
                (c or conv.frompy(a.type)[0]).format('PyTuple_GET_ITEM(args,{0})'.format(i)) for i,a,c in zip(itertools.count(),self.call[1],argconv)))

    def get_code(self,conv,argconv = [],skipsize = 0,ind = Tab(2),exactlenchecked = False):
        anychildnodes = any(self.basic.itervalues()) or self.objects

        assert anychildnodes or self.call

        r = ''
        get_size = '{0}if(PyTuple_GET_SIZE(args) {1} {2}) {{\n'

        if skipsize:
            assert anychildnodes

            r += self.basic_and_objects_code(conv,argconv,skipsize-1,ind,exactlenchecked)
            if self.call:
                r += self.call_code(conv,argconv,ind)

        elif anychildnodes:
            # if the exact length was tested, "skipsize" should cover the rest of the arguments
            assert not exactlenchecked

            min_args = self.min_arg_length()
            max_args = self.max_arg_length()

            if min_args == max_args:
                r += get_size.format(ind,'==',len(argconv) + min_args)
                ind += 1

                r += self.basic_and_objects_code(conv,argconv,min_args - 1,ind,True)
                if self.call:
                    r += self.call_code(conv,argconv,ind)

                ind -= 1
                r += ind + '}\n'
            else:
                r += get_size.format(ind,'>=',len(argconv) + min_args)
                ind += 1

                r += self.basic_and_objects_code(conv,argconv,min_args - 1,ind)

                ind -= 1
                r += ind + '}\n'

                if self.call:
                    r += self.call_code(conv,argconv,ind)

        elif exactlenchecked:
            assert self.call
            r += self.call_code(conv,argconv,ind)

        else:
            assert self.call
            r += get_size.format(ind,'==',len(argconv))
            r += self.call_code(conv,argconv,ind + 1)
            r += ind + '}\n'

        return r





class Conversion:
    def __init__(self,tns):
        # get the types specified by the typedefs
        for x in ("bool","sint","uint","sshort","ushort","slong","ulong",
                  "float","double","long_double","size_t","schar","uchar",
                  "char","wchar_t","py_unicode","void","stdstring",
                  "stdwstring"):
            setattr(self,x,tns.find("type_"+x).type)

        try:
            for x in ("slonglong","ulonglong"):
                setattr(self,x,tns.find("type_"+x).type)
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
            self.stdstring : 'StringToPy({0})'
        }

        self.basic_types = {
            TYPE_INT : set([self.sshort,self.ushort,self.sint,self.uint,self.slong]),
            TYPE_LONG : set([self.ulong,self.size_t]),
            TYPE_FLOAT : set([self.float,self.double,self.long_double]),
            TYPE_STR : set([self.cstring,self.stdstring]),
            TYPE_UNICODE : set([self.cwstring,self.stdwstring])
        }

        self.basic_types[TYPE_LONG if self.uint.size == self.ulong.size else TYPE_INT].add(self.slong)


        tod = 'PyToDouble({0})'
        self.__frompy = {
            self.sshort : 'PyToShort({0})',
            self.ushort : 'PyToUShort({0})',
            self.sint : 'PyToInt({0})',
            self.uint : 'PyToUInt({0})',
            self.slong : 'PyToLong({0})',
            self.ulong : 'PyToULong({0})',
            self.float : 'static_cast<float>(PyToDouble({0}))',
            self.double : tod,
            self.long_double : tod
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

            self.__frompy[self.slonglong] = 'PyToLongLong({0})'
            self.__frompy[self.ulonglong] = 'PyToULongLong({0})'

            self.__pymember[self.slonglong] = 'T_LONGLONG'
            self.__pymember[self.ulonglong] = 'T_ULONGLONG'

        self.cppclasstopy = {}

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

        r = self.__frompy.get(t)
        if r: return r,t

        # if t is a pointer or reference to a const datatype, we can pass the address of, or a reference to, the underlying datatype
        if isinstance(t,gccxml.CPPReferenceType):
            if const_qualified(t.type):
                nt = t.type.type
                r = self.__frompy.get(nt)
                if r: return r, nt
        elif isinstance(t,gccxml.CPPPointerType):
            if const_qualified(t.type):
                nt = t.type.type
                r = self.__frompy.get(nt)
                if r: return '*({0})'.format(r), nt
        elif isinstance(t,gccxml.CPPCvQualifiedType):
            r = self.__frompy(t)
            if r: return r, t.type

        raise SpecificationError('No conversion from "PyObject*" to "{0}" is registered'.format(t.typestr()))

    def member_macro(self,t):
        try:
            return self.__pymember[t]
        except LookupError:
            # maybe this should be done automatically
            raise SpecificationError('A member of type "{0}" cannot be exposed directly. Define a getter and/or setter for this value instead.'.format(t.typestr()))

    def check_and_cast(self,t):
        cdef = self.cppclasstopy[strip_refptr(t)]
        check ='PyObject_TypeCheck({{0}},&obj_{0}Type)'.format(cdef.name)
        cast = '{0}reinterpret_cast<obj_{1}*>({{0}})->base'.format('&' if isinstance(t,gccxml.CPPPointerType) else '',cdef.name)
        return check,cast

    def arg_parser(self,args,use_kwds = True,indent = None):
        # even if we are not taking any arguments, get_arg::finish should still be called (to report an error if arguments were received)

        if indent is None: indent = ' ' * 8

        prep = '{0}get_arg ga(args,{1});\n'.format(indent,'kwds' if use_kwds else '0')

        if any(a.default for a in args):
            prep += indent + 'PyObject *temp;\n'

        for i,a in enumerate(args):
            frompy, frompytype = self.frompy(a.type)
            var = frompytype.typestr("_{0}".format(i))
            name = '"{0}"'.format(a.name) if a.name and use_kwds else '0'
            if a.default:
                prep += '{0}temp = ga({1},false);\n{0}{2} = temp ? {3} : {4};\n'.format(indent,name,var,frompy.format('temp'),a.defult)
            else:
                prep += '{0}{1} = {2};\n'.format(indent,var,frompy.format('ga({0},true)'.format(name)))

        prep += indent + 'ga.finished();\n'

        return cppcode(','.join('_{0}'.format(i) for i in range(len(args))), prep)

    def function_call(self,calls,use_kwds = True):
        """Generate code to call one function from a list of overloads.

        calls -- A sequence of tuples containing a function (str) and a list of arguments
        use_kwds -- whether keyword arguments are available or not (does not apply if len(calls) is greater than 1)

        Caveat: only position arguments are checked, unless len(calls) == 1. Use of keywords will result in an exception being thrown.

        Caveat: the resulting algorithm for overload resolution is different from the C++ standard. It will compare one argument at a time
        and will stop after finding a viable match. The parallel arguments are sorted from most to least specific, however given classes S
        and B, where B is the base class of S, if there are two overloads S,B,B and B,S,S and the arguments given are S,S,S then S,B,B will
        be chosen because the first argument was a better match. The same limitation applies when S and B are built-in types that can be
        converted to one-another (unicode vs str and float vs int vs long).

        """
        assert calls

        if len(calls) == 1:
            code = self.arg_parser(calls[0][1],use_kwds)
            return '{0}        {1}({2});\n'.format(code.prep,calls[0][0],code.val)

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

        tree = self.generate_arg_tree([(x[1],x) for x in ovlds])
        tree.sort_objects()

        return tmpl.overload_func_call.format(
            inner = tree.get_code(self),
            nokwdscheck = use_kwds)

    def add_conv(self,t,to,from_):
        if to: self.__topy[t] = to
        if from_: self.__frompy[t] = from_

    def closest_type_is_pytype(self,t,py):
        s = self.basic_types[py]
        return (t in s) or isinstance(t,(gccxml.CPPPointerType,gccxml.CPPReferenceType)) and (strip_cvq(t.type) in s)

    def generate_arg_tree(self,argss):
        argss.sort(key = lambda x: len(x[0]) and x[0][0].type.typestr())

        node = ArgBranchNode()
        for k,g in itertools.groupby(argss,lambda x: bool(x[0]) and x[0][0].type):
            if k:
                subnode = self.generate_arg_tree([(x[1:],orig) for x,orig in g])

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
        self.members = []
        self.doc = ''

    def _filtered_members(self,type):
        return filter(lambda x: isinstance(x,type), self.members)

    def print_gccxml_input(self,out):
        # In addition to the include files, declare certain typedefs so they can be matched against types used elsewhere
        print >> out, tmpl.gccxmlinput_start.format(self._formatted_includes(),TEST_NS)

        # declare a bunch of dummy functions with the arguments we want gccxml to parse for us
        for i,x in enumerate(self._funcs_with_overload()):
            print >> out, 'void dummy_func_{0}({1});\n'.format(i,x.overload)

        print >> out, '}\n'

    def _collect_overload_arg_lists(self,tns):
        for i,x in enumerate(self._funcs_with_overload()):
            f = tns.find('dummy_func_{0}'.format(i))
            assert isinstance(f,gccxml.CPPFunction)
            x.overload = f.args

    def _formatted_includes(self):
        return "\n".join('#include "{0}"'.format(i) for i in self.includes)

    def write_file(self,path,cppint):
        tns = cppint.find(TEST_NS)

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

        for m in self.members:
            if isinstance(m,ClassDef):
                c = cppint.find(m.type)
                print >> out.cpp, tmpl.get_base.format(
                    type = c.typestr(),
                    name = m.name)

                # these assume the class has copy constructors
                conv.add_conv(c,'new obj_{0}({{0}})'.format(m.name),'get_base_{0}({{0}})'.format(m.name))
                conv.cppclasstopy[c] = m

        for m in self.members:
            m.output(out,self.name,cppint)

        print >> out.cpp, tmpl.module_init.format(
            funclist = '',
            module = self.name)

        for c in self._filtered_members(ClassDef):
            print >> out.cpp, tmpl.module_class_prepare.format(c.name)

        print >> out.cpp, tmpl.module_create.format(
            name = self.name,
            doc = quote_c(self.doc) if self.doc else "0")

        for c in self._filtered_members(ClassDef):
            print >> out.cpp, tmpl.module_class_add.format(c.name)

        print >> out.cpp, tmpl.module_end
        print >> out.h, tmpl.header_end

    def _funcs_with_overload(self):
        '''yields function-like objects that have a non-empty overload defined'''

        for m in self.members:
            if isinstance(m,ClassDef):
                for x in m.constructors:
                    if x.overload: yield x
                for meth in m.methods:
                    if x.overload: yield x
            elif isinstance(m,DefDef):
                if m.overload: yield m

def stripsplit(x):
    return [i.strip() for i in x.split(",")]

class tag_Class(tag):
    def __init__(self,args):
        self.r = ClassDef()
        self.r.type = args["type"]
        self.r.name = args.get("name",self.r.type)

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
            self.r.methods.append(data)

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
        self.r.overload = args.get("overload")

class tag_Module(tag):
    def __init__(self,args):
        self.r = ModuleDef()
        self.r.name = args["name"]
        self.r.includes = stripsplit(args["include"])

    def child(self,name,data):
        if name == "class" or name == "def":
            self.r.members.append(data)
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
        self.r.name = args.get('name',self.r.cmember)
        self.r.readonly = args.get('readonly',False)
        if self.r.readonly is not False:
            try:
                self.r.readonly = {'true':True,'false':False}[self.r.readonly.lower()]
            except LookupError:
                raise ParseError('The value of "readonly" must be either "true" or "false"')


def get_ret_semantic(args):
    rs = args.get("return-semantic")
    if rs is not None:
        mapping = {"copy" : RET_COPY, "default" : None}
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
        self.r = DefDef()
        self.r.func = args["func"]
        self.r.name = args.get("name",self.r.func)
        self.r.retsemantic = get_ret_semantic(args)
        self.r.overload = args.get("overload")

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
