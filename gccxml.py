import itertools

from xmlparse import tag,parse,ParseError


ACCESS_PUBLIC = 1
ACCESS_PROTECTED = 2
ACCESS_PRIVATE = 3

def parse_access(a):
    return {'public':ACCESS_PUBLIC, 'protected':ACCESS_PROTECTED, 'private':ACCESS_PRIVATE}[a]

def link_item(*args):
    def inner(self,items):
        for arg in args:
            setattr(self,arg,items[getattr(self,arg)])
    return inner

def link_list(arg):
    def inner(self,items):
        setattr(self,arg,filter(None,(items.get(id) for id in getattr(self,arg))))
    return inner

class ArgList(list):
    def __str__(self):
        return ','.join(map(str,self))



class CPPType(object):
    __slots__ = 'typestr_cache'

    def __hash__(self):
        return hash(self.typestr())

    def __eq__(self,b):
        if isinstance(b,CPPType): return self.typestr() == b.typestr()
        return NotImplemented

    def __ne__(self,b):
        if isinstance(b,CPPType): return self.typestr() != b.typestr()
        return NotImplemented

    def typestr(self,deriv=''):
        if deriv:
            return self._typestr(deriv)

        if not hasattr(self,'typestr_cache'):
            self.typestr_cache = self._typestr('')

        return self.typestr_cache

class CPPBasicType(CPPType):
    __slots__ = 'name'

    def __init__(self,name = None):
        self.name = name

    def _typestr(self,deriv):
        return '{0} {1}'.format(self.name,deriv) if deriv else self.name

class CPPClass(CPPBasicType):
    __slots__ = 'bases','members','size'

    def __init__(self,name = None):
        self.name = name
        self.bases = []

    def link(self,items):
        self.members = filter(None,(items.get(id) for id in self.members))
        for b in self.bases: b.link(items)

class CPPArgument(object):
    __slots__ = 'name','type','default'

    link = link_item("type")

    def __str__(self):
        return self.type.typestr()

class CPPBase(object):
    __slots__ = 'type','access'
    link = link_item("type")

class CPPEllipsis(object):
    __slots__ = ()

    def __str__(self):
        return '...'

    def link(self,items):
        pass

cppellipsis = CPPEllipsis() # only one instance is needed

class CPPFunctionType(CPPType):
    __slots__ = 'args','returns'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        self.returns = items[self.returns]
        for a in self.args: a.link(items)

    def _typestr(self,deriv):
        return '{0} ({1})({2})'.format(self.returns.typestr(),deriv,','.join(map(str,self.args)))

class CPPFunction(object):
    __slots__ = 'name','returns','args'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        self.returns = items[self.returns]
        for a in self.args: a.link(items)

class CPPPointerType(CPPType):
    __slots__ = 'type','size'

    def __init__(self,type = None):
        self.type = type

    link = link_item('type')

    def _typestr(self,deriv):
        return self.type.typestr('*'+deriv)

class CPPFundamentalType(CPPBasicType):
    __slots__ = 'size'

    def link(self,items):
        pass

class CPPNamespace(object):
    __slots__ = 'name','members'
    link = link_list('members')

class CPPField(object):
    __slots__ = 'name','type','access','offset','static'
    link = link_item('type')

class CPPConstructor(object):
    __slots__ = 'access','args','artificial'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        for a in self.args: a.link(items)

class CPPMethod(object):
    __slots__ = 'name','returns','access','const','virtual','pure_virtual','static','args'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        self.returns = items[self.returns]
        for a in self.args: a.link(items)

class CPPMethodType(CPPType):
    __slots__ = 'basetype','returns','args','const'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        self.returns = items[self.returns]
        self.basetype = items[self.basetype]
        for a in self.args: a.link(items)

    def _typestr(self,deriv):
        return '{0} ({1}::{2})({3}){4}'.format(self.returns.typestr(),self.basetype.typestr(),deriv,",".join(self.args)," const" if self.const else "")

class CPPOperatorMethod(CPPMethod):
    __slots__ = ()

class CPPArrayType(CPPType):
    __slots__ = 'type','max'

    link = link_item('type')

    def _typestr(self,deriv):
        part = '[{0}]'.format(self.max+1 if self.max else '')
        return self.type.typestr('({0}){1}'.format(deriv,part) if deriv else part)

class CPPReferenceType(CPPType):
    __slots__ = 'type','size'

    def __init__(self,type = None):
        self.type = type

    link = link_item('type')

    def _typestr(self,deriv):
        return self.type.typestr('&'+deriv)

class CPPCvQualifiedType(CPPType):
    __slots__ = 'type','const','volatile','restrict'

    def __init__(self,type = None,const = False,volatile = False,restrict = False):
        self.type = type
        self.const = const
        self.volatile = volatile
        self.restrict = restrict

    link = link_item('type')

    def _typestr(self,deriv):
        # Because of the way CPPBasicType works, these qualifiers end up on the right side of types (e.g. "int const"). If one wants, this
        # can be easily changed by testing isinstance(self.type,CPPBasicType) and outputting "{qualifiers} {self.type.name} {deriv}"
        a = []
        if deriv: a.append(deriv)
        if self.restrict: a.insert(0,'restrict')
        if self.volatile: a.insert(0,'volatile')
        if self.const: a.insert(0,'const')
        return self.type.typestr(' '.join(a))

class CPPUnion(CPPBasicType):
    __slots__ = 'size','members'
    link = link_list('members')

class CPPDestructor(object):
    __slots__ = 'name','access','virtual'

    def link(self,items):
        pass

class CPPOffsetType(CPPType):
    __slots__ = 'basetype','type','size'
    link = link_item('basetype','type')

    def _typestr(self,deriv):
        return '{0} ({1}::{2})'.format(self.returns.typestr(),self.basetype.typestr(),deriv)

class CPPTypeDef(CPPType):
    __slots__ = 'name','type'
    link = link_item('type')

    def _typestr(self,deriv):
        return self.type.typestr(deriv)

class CPPEnumeration(CPPBasicType):
    __slots__ = 'size'

    def link(self,items):
        pass




class _no_default: pass
no_default = _no_default()

def zero_one(x):
    return bool(int(x))

def common_init(OType,keys):
    def inner(self,args):
        o = OType()
        self.r = args["id"],o
        for k in keys:
            default = no_default
            if isinstance(k,tuple):
                attr = k[0]
                f = k[1]
                if len(k) > 2: default = k[2]
            else:
                attr = k
                f = None

            input = args.get(attr)
            if input is None:
                if default is no_default:
                    raise ParseError('The required attribute "{0}" was not found'.format(attr))
                input = default
            elif f:
                input = f(input)

            setattr(o,attr,input)

    return inner

def bool_keys(*keys):
    return [(k,zero_one,False) for k in keys]

def function_child(self,name,data):
    if name == 'Argument' or name == 'Ellipsis':
        self.r[1].args.append(data)



class tag_Class(tag):
    __init__ = common_init(CPPClass,[('name',None,None),('size',None,None),('members',unicode.split,[])])

    def child(self,name,data):
        if name == "Base":
            self.r[1].bases.append(data)

class tag_Argument(tag):
    def __init__(self,args):
        self.r = CPPArgument()
        self.r.name = args.get("name")
        self.r.type = args["type"]
        self.r.default = args.get("default")

class tag_Base(tag):
    def __init__(self,args):
        self.r = CPPBase()
        self.r.type = args["type"]
        self.r.access = parse_access(args["access"])

class tag_Function(tag):
    __init__ = common_init(CPPFunction,["name","returns"])
    child = function_child

class tag_PointerType(tag):
    __init__ = common_init(CPPPointerType,["type","size"])

class tag_FundamentalType(tag):
    __init__ = common_init(CPPFundamentalType,[
        "name",
        ("size",None,None)]) # the type "void" does not have a size

class tag_FunctionType(tag):
    __init__ = common_init(CPPFunctionType,["returns"])
    child = function_child

class tag_Namespace(tag):
    __init__ = common_init(CPPNamespace,["name",("members",unicode.split)])

class tag_Field(tag):
    __init__ = common_init(CPPField,["name","type",("access",parse_access),"offset"] + bool_keys("static"))

class tag_Method(tag):
    __init__ = common_init(CPPMethod,["name","returns",("access",parse_access)] + bool_keys("const","virtual","pure_virtual","static"))
    child = function_child

class tag_Constructor(tag):
    __init__ = common_init(CPPConstructor,[("access",parse_access)] + bool_keys('artificial'))
    child = function_child

class tag_OperatorMethod(tag_Method):
    pass

class tag_ArrayType(tag):
    __init__ = common_init(CPPArrayType,["type",("max",lambda x: int(x.rstrip("u")) if x else None)])

class tag_ReferenceType(tag):
    __init__ = common_init(CPPReferenceType,["type","size"])

class tag_CvQualifiedType(tag):
    __init__ = common_init(CPPCvQualifiedType,["type"] + bool_keys("const","volatile","restrict"))

class tag_MethodType(tag):
    __init__ = common_init(CPPMethodType,["basetype","returns"] + bool_keys("const"))
    child = function_child

class tag_OperatorFunction(tag_Function):
    pass

class tag_Union(tag):
    __init__ = common_init(CPPUnion,["size",("name",None,None),("members",unicode.split)])

class tag_Destructor(tag):
    __init__ = common_init(CPPDestructor,["name",("access",parse_access)] + bool_keys("virtual"))

class tag_OffsetType(tag):
    __init__ = common_init(CPPOffsetType,["basetype","type","size"])

class tag_TypeDef(tag):
    __init__ = common_init(CPPTypeDef,["name","type"])

class tag_Enumeration(tag):
    __init__ = common_init(CPPEnumeration,["name","size"])

class tag_Ellipsis(tag):
    def __init__(self,args):
        self.r = cppellipsis


class tag_root(tag):
    def __init__(self,args):
        self.r = {}

    def child(self,name,data):
        if name in tagdefs and data:
            self.r[data[0]] = data[1]


tagdefs = {
    "Class" : tag_Class,
    "Struct" : tag_Class,
    "Argument" : tag_Argument,
    "Base" : tag_Base,
    "Function" : tag_Function,
    "PointerType" : tag_PointerType,
    "FundamentalType" : tag_FundamentalType,
    "FunctionType" : tag_FunctionType,
    "Namespace" : tag_Namespace,
    "Ellipsis" : tag_Ellipsis,
    "Field" : tag_Field,
    "Method" : tag_Method,
    "Constructor" : tag_Constructor,
    "OperatorMethod" : tag_OperatorMethod,
    "ArrayType" : tag_ArrayType,
    "ReferenceType" : tag_ReferenceType,
    "CvQualifiedType" : tag_CvQualifiedType,
    "MethodType" : tag_MethodType,
    "OperatorFunction" : tag_OperatorFunction,
    "Destructor" : tag_Destructor,
    "Union" : tag_Union,
    "OffsetType" : tag_OffsetType,
    "GCC_XML" : tag_root,
    "Typedef" : tag_TypeDef,
    "Enumeration" : tag_Enumeration,

    # don't care about these (yet):
    "Variable" : tag,
    "Converter" : tag,
    "EnumValue" : tag,
    "File" : tag
}



def getinterface(path):
    rootnamespace = None
    items = parse(path,tagdefs)
    for i in items.itervalues():
        i.link(items)
        if isinstance(i,CPPNamespace) and i.name == "::":
            rootnamespace = i

    return rootnamespace
