
import xml.parsers.expat
import sys
import traceback

import err

class ParseError(err.Error):
    def __str__(self):
        return 'Parsing Error: ' + super(ParseError,self).__str__()

class ArgProxy(object):
    def __init__(self,args):
        self.args = args

    def get(self,key,val=None):
        return self.args.get(key,val)

    def __getitem__(self,key):
        try:
            return self.args[key]
        except KeyError:
            raise ParseError('Required attribute "{0}" is missing'.format(key))

    def __contains__(self,key):
        return key in self.args


def tag_handler(tagname,tagclass):
    def inner(func):
        th = getattr(func,'_tag_handler',None)
        if not th:
            th = []
            func._tag_handler = th
        th.append((tagname,tagclass))
        return func
    return inner


class TagMeta(type):
    def __init__(cls,name,bases,dct):
        super(TagMeta,cls).__init__(name,bases,dct)
        handlers = getattr(cls,'tag_handlers',None)

        for b in bases:
            bh = getattr(b,'tag_handlers',None)
            if bh: handlers.update(bh)

        if not handlers:
            handlers = {}
            cls.tag_handlers = handlers
        for func in dct.itervalues():
            h = getattr(func,'_tag_handler',None)
            if h:
                for tagname,tagclass in h:
                    handlers[tagname] = tagclass,func


class tag(object):
    __metaclass__ = TagMeta
    r = None
    def __init__(self,args):
        pass

    def end(self):
        return self.r

    def text(self,data):
        if(data and not data.isspace()):
            raise ParseError('Unexpected text')


def parse(path,toplevelname,toplevel):
    class TopLevel(tag):
        @tag_handler(toplevelname,toplevel)
        def accept(self,data):
            self.value = data

    stack = [(TopLevel(None),None)]
    p = xml.parsers.expat.ParserCreate()

    def add_to_except(e):
        e.info['file'] = path
        e.info['line #'] = p.CurrentLineNumber

    def start_tag(name,args):
        handlers = getattr(stack[-1][0].__class__,'tag_handlers',{})
        try:
            c,f = handlers[name]
        except KeyError:
            raise ParseError('unexpected tag "{0}"'.format(name))
        try:
            t = c(ArgProxy(args))
        except ParseError as e:
            add_to_except(e)
            raise

        stack.append((t,f))

    def end_tag(name):
        t,f = stack.pop()
        try:
            f(stack[-1][0],t.end())
        except ParseError as e:
            add_to_except(e)
            raise

    def text(data):
        try:
            stack[-1][0].text(data)
        except ParseError as e:
            add_to_except(e)
            raise

    p.StartElementHandler = start_tag
    p.EndElementHandler = end_tag
    p.CharacterDataHandler = text
    p.ParseFile(open(path))

    assert len(stack) == 1
    return stack[0][0].value