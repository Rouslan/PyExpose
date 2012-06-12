
import sys
import itertools
import operator
import copy

import gccxml
from err import SpecificationError

__all__ = ('RET_MANAGED_REF','RET_MANAGED_PTR','RET_UNMANAGED_REF','RET_COPY',
           'RET_SELF','mandatory_args','compatible_args','accepts_args',
           'always_true','BaseMembers','base_count','cconst','cptr','strip_cvq',
           'strip_refptr','is_const','can_throw','default_to_ov')



RET_MANAGED_REF = 1
RET_MANAGED_PTR = 2
RET_UNMANAGED_REF = 3
RET_COPY = 1001
RET_SELF = 1002


def mandatory_args(x):
    return len(list(itertools.takewhile(lambda a: a.default is None, x.args)))

def compatible_args(f,given):
    """Return a copy of f with the subset of arguments from 'needed', specified
    by 'given' or None if the arguments don't match."""
    if not accepts_args(f,[a.type for a in given]): return None
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

def is_const(x):
    return isinstance(x,gccxml.CPPCvQualifiedType) and x.const

def can_throw(x):
    assert isinstance(x,(gccxml.CPPFunction,gccxml.CPPMethod))
    return not (x.throw == '' or 'nothrow' in x.attributes)


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


def getDestructor(self):
    for m in self.members:
        if isinstance(m,gccxml.CPPDestructor):
            return m
    return None
gccxml.CPPClass.getDestructor = getDestructor


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
