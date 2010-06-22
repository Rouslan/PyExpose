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

    def __repr__(self):
        return 'ArgList(({0}))'.format(','.join(map(repr,self)))



class CPPSymbol(object):
    __slots__ = ()

    @property
    def canon_name(self):
        return self.name

    @property
    def full_name(self):
        n = []
        if self.context:
            n.append(self.context.full_name)
        n.append(self.canon_name)

        return '::'.join(filter(None,n))

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

class CPPBasicType(CPPType,CPPSymbol):
    __slots__ = 'name','context'

    def __init__(self,name = None):
        self.name = name
        self.context = None

    def _typestr(self,deriv):
        return '{0} {1}'.format(self.full_name,deriv) if deriv else self.full_name

class CPPClass(CPPBasicType):
    __slots__ = 'bases','members','size'

    def __init__(self,name = None):
        self.name = name
        self.bases = []
        self.members = []

    def link(self,items):
        for b in self.bases: b.link(items)

    def __repr__(self):
        return '<CPPClass: {0}>'.format(self.name or '*anonymous*')

class CPPArgument(object):
    __slots__ = 'name','type','default'

    def __init__(self,type,name=None,default=None):
        self.type = type
        self.name = name
        self.default = default

    link = link_item("type")

    def __str__(self):
        return self.type.typestr()

    def __repr__(self):
        s = self.type.typestr() if hasattr(self.type,'typestr') else str(self.type)
        if self.name: s += ' ' + self.name
        if self.default: s += '=' + self.default
        return '<{0}>'.format(s)

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

class CPPFunction(CPPSymbol):
    __slots__ = 'name','returns','args','context'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        self.returns = items[self.returns]
        for a in self.args: a.link(items)

    def __repr__(self):
        return '<CPPFunction: {0}>'.format(self.name) if hasattr(self,'name') else '<CPPFunction>'

class CPPOperatorFunction(CPPFunction):
    __slots__ = ()

    @property
    def canon_name(self):
        return 'operator ' + self.name

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

class CPPNamespace(CPPSymbol):
    __slots__ = 'name','members','context'

    def __init__(self):
        self.members = []

    def link(self,items):
        pass

class CPPField(CPPSymbol):
    __slots__ = 'name','type','access','offset','static','context'
    link = link_item('type')

class CPPConstructor(CPPSymbol):
    __slots__ = 'name','access','args','artificial','context'

    def __init__(self):
        self.args = ArgList()

    def link(self,items):
        for a in self.args: a.link(items)

class CPPMethod(CPPSymbol):
    __slots__ = 'name','returns','access','const','virtual','pure_virtual','static','args','context'

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

    @property
    def canon_name(self):
        return 'operator ' + self.name

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
    def __init__(self):
        self.members = []

    def link(self,items):
        pass

class CPPDestructor(CPPSymbol):
    __slots__ = 'name','access','virtual','context','artificial'

    def link(self,items):
        pass

    @property
    def canon_name(self):
        return '~' + self.name

class CPPOffsetType(CPPType):
    __slots__ = 'basetype','type','size'
    link = link_item('basetype','type')

    def _typestr(self,deriv):
        return '{0} ({1}::{2})'.format(self.returns.typestr(),self.basetype.typestr(),deriv)

class CPPTypeDef(CPPType,CPPSymbol):
    __slots__ = 'name','type','context'
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

def common_init(keys):
    def inner(self,args):
        o = self.OType()
        self.r = args['id'],o

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
    OType = CPPClass
    __init__ = common_init([('name',None,None),('size',None,None),'context'])

    def child(self,name,data):
        if name == "Base":
            self.r[1].bases.append(data)

class tag_Argument(tag):
    def __init__(self,args):
        self.r = CPPArgument(
            args["type"],
            args.get("name"),
            args.get("default"))

class tag_Base(tag):
    def __init__(self,args):
        self.r = CPPBase()
        self.r.type = args["type"]
        self.r.access = parse_access(args["access"])

class tag_Function(tag):
    OType = CPPFunction
    __init__ = common_init(["name","returns",'context'])
    child = function_child

class tag_PointerType(tag):
    OType = CPPPointerType
    __init__ = common_init(["type","size"])

class tag_FundamentalType(tag):
    OType = CPPFundamentalType
    __init__ = common_init([
        "name",
        ("size",None,None)]) # the type "void" does not have a size

class tag_FunctionType(tag):
    OType = CPPFunctionType
    __init__ = common_init(["returns"])
    child = function_child

class tag_Namespace(tag):
    OType = CPPNamespace
    __init__ = common_init([("name",lambda x: None if x == '::' else x),('context',None,None)])

class tag_Field(tag):
    OType = CPPField
    __init__ = common_init(["name","type",("access",parse_access),"offset",'context'] + bool_keys("static"))

class tag_Method(tag):
    OType = CPPMethod
    __init__ = common_init(["name","returns",("access",parse_access),'context'] + bool_keys("const","virtual","pure_virtual","static"))
    child = function_child

class tag_Constructor(tag):
    OType = CPPConstructor
    __init__ = common_init([('name',None,None),("access",parse_access),'context'] + bool_keys('artificial'))
    child = function_child

class tag_OperatorMethod(tag_Method):
    OType = CPPOperatorMethod

class tag_ArrayType(tag):
    OType = CPPArrayType
    __init__ = common_init(["type",("max",lambda x: int(x.rstrip("u")) if x else None)])

class tag_ReferenceType(tag):
    OType = CPPReferenceType
    __init__ = common_init(["type","size"])

class tag_CvQualifiedType(tag):
    OType = CPPCvQualifiedType
    __init__ = common_init(["type"] + bool_keys("const","volatile","restrict"))

class tag_MethodType(tag):
    OType = CPPMethodType
    __init__ = common_init(["basetype","returns"] + bool_keys("const"))
    child = function_child

class tag_OperatorFunction(tag_Function):
    OType = CPPOperatorFunction

class tag_Union(tag):
    OType = CPPUnion
    __init__ = common_init(["size",("name",None,None),("members",unicode.split),'context'])

class tag_Destructor(tag):
    OType = CPPDestructor
    __init__ = common_init(['name',("access",parse_access),'context'] + bool_keys('virtual','artificial'))

class tag_OffsetType(tag):
    OType = CPPOffsetType
    __init__ = common_init(["basetype","type","size"])

class tag_TypeDef(tag):
    OType = CPPTypeDef
    __init__ = common_init(["name","type",'context'])

class tag_Enumeration(tag):
    OType = CPPEnumeration
    __init__ = common_init(["name","size",'context'])

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

    # fill "members" using context, because the "members" list doesn't seem to list all members
    for item in items.itervalues():
        item.link(items)
        if hasattr(item,'context'):
            if item.context is None:
                if isinstance(item,CPPNamespace): rootnamespace = item
            else:
                c = items[item.context]
                c.members.append(item)
                item.context = c

    return rootnamespace
