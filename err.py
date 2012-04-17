import sys


__all__ = 'Error','SpecificationError','emit_warning'


class Error(Exception):
    def __init__(self,msg,info=None):
        super(Error,self).__init__(msg,info or {})

    @property
    def msg(self):
        return self.args[0]

    @property
    def info(self):
        return self.args[1]

    def __str__(self):
        return self.msg + ''.join('\n  {0}: {1}'.format(*i) for i in self.info.iteritems())


class SpecificationError(Error):
    def __str__(self):
        return 'Specification Error: ' + super(SpecificationError,self).__str__()


warnings_are_errors = False

def emit_warning(exc):
    if warnings_are_errors:
        raise exc

    print >> sys.stderr, exc
