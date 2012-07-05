
import sys
import itertools

from . import gccxml
from . import espectmpl as tmpl
from .cpptypes import *
from .err import SpecificationError


TO_PY_FUNC = '__py_to_pyobject__'
FROM_PY_FUNC = '__py_from_pyobject__'
TRAVERSE_FUNC = '__py_traverse__'
CLEAR_FUNC = '__py_clear__'
CAST_AS_MEMBER_FIELD = '__py_cast_as_member_t__'


TYPE_FLOAT = 1
TYPE_INT = 2
TYPE_LONG = 3
TYPE_STR = 4
TYPE_UNICODE = 5
TYPES_LIST = range(1,6)


CHECK_FLOAT = 0b100
CHECK_INT = 0b010
CHECK_LONG = 0b001

# A table specifying what checks to make when matching a Python number to a specific C++ overload
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

    def get_code(self,conv,argconv = [],skipsize = 0,ind = tmpl.Tab(2),get_arg = lambda x: 'PyTuple_GET_ITEM(args,{0})'.format(x),exactlenchecked = False):
        anychildnodes = any(self.basic.itervalues()) or self.objects

        assert anychildnodes or self.call

        r = ''
        get_size = ind.line('if(PyTuple_GET_SIZE(args) {0} {1}) {{')

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
                r += get_size.format('==',len(argconv) + min_args)
                ind += 1

                r += self.basic_and_objects_code(conv,argconv,min_args - 1,ind,get_arg,True)
                if self.call:
                    r += self.call_code(conv,argconv,ind,get_arg)

                ind -= 1
                r += ind.line('}')
            else:
                r += get_size.format('>',len(argconv))

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
            r += get_size.format('==',len(argconv))
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

        If one isn't found in self.__frompy, check if t has a member function
        with the name given by "FROM_PY_FUNC".
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
            if r and (r[0] or is_const(t.type)):
                return r[1], ref(nt)
        elif isinstance(t,gccxml.CPPPointerType):
            nt = strip_cvq(t.type)
            r = self.__frompy_base(nt)
            if r and(r[0] or is_const(t.type)):
                return '&({0})'.format(r[1]), cptr(nt)
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
            if hasattr(t,'lookup'):
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

    def arg_parser(self,args,use_kwds = True,indent = tmpl.Tab(2)):
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

    def arg_parser_b(self,args,use_kwds=True,indent=tmpl.Tab(2)):
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

    def function_call(self,calls,errval='0',use_kwds=True,ind=tmpl.Tab(2)):
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
            return prep + tmpl.Tab(2).line(calls[0][0].output(args,tmpl.Tab(2)))

        r = (self.function_call_fallthrough(calls,ind) +
            tmpl.no_such_overload.format(args='args',errval=errval))
        if use_kwds: r = tmpl.no_keywords_check + r
        return r

    def function_call_fallthrough(self,calls,ind=tmpl.Tab(2)):
        # turn default values into overloads
        ovlds = []
        for f,args in calls:
            ovlds.extend((f,newargs) for newargs in default_to_ov(args))

        return self.generate_arg_tree(ovlds).get_code(self,ind=ind)

    def function_call_narg_fallthrough(self,calls,vars,ind=tmpl.Tab(2)):
        assert calls
        return self.generate_arg_tree(calls).basic_and_objects_code(
            self,[],len(vars)-1,ind,lambda x: vars[x],True)

    def function_call_narg(self,calls,vars,errval='0',ind=tmpl.Tab(2)):
        if len(calls) == 1:
            return ind + calls[0][0].output(
                [self.frompy(a.type)[0].format(v) for a,v in zip(calls[0][1],vars)],
                ind)

        return (self.function_call_narg_fallthrough(calls,vars,ind) +
            tmpl.no_such_overload.format(args=','.join(vars),errval=errval))

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
