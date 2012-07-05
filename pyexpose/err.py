import sys


__all__ = 'Error','SpecificationError','WARN_ERROR','WARN_NORMAL','WARN_MINOR','emit_warning'


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


WARN_ERROR = 3 # it's wrong but we can still generate the code
WARN_NORMAL = 2 # probably a mistake
WARN_MINOR = 1 # could be a mistake

error_level = WARN_ERROR
ignore_level = 0

def emit_warning(level,msg):
    if level >= error_level:
        raise SpecificationError(msg)

    if level > ignore_level:
        print >> sys.stderr, 'Warning: '+msg
