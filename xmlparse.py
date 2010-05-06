
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

class tag(object):
    r = None
    def __init__(self,args):
        pass

    def end(self):
        return self.r

    def child(self,name,data):
        pass

    def text(self,data):
        pass

def parse(path,tagdefs):
    stack = []
    p = xml.parsers.expat.ParserCreate()

    def add_to_except(e):
        e.info['file'] = path
        e.info['line #'] = p.CurrentLineNumber

    def start_tag(name,args):
        try:
            c = tagdefs[name]
        except KeyError:
            raise ParseError('unexpected tag "{0}"'.format(name))
        try:
            t = c(ArgProxy(args))
        except ParseError as e:
            add_to_except(e)
            raise

        stack.append(t)

    def end_tag(name):
        if len(stack) > 1:
            top = stack.pop()
            try:
                stack[-1].child(name,top.end())
            except ParseError as e:
                add_to_except(e)
                raise

    def text(data):
        if stack: stack[-1].text(data)


    p.StartElementHandler = start_tag
    p.EndElementHandler = end_tag
    p.CharacterDataHandler = text
    p.ParseFile(open(path))

    return stack[-1].end()