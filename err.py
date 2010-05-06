

class Error(Exception):
    def __init__(self,msg,**info):
        super(Error,self).__init__(msg,info)

    @property
    def msg(self):
        return self.args[0]

    @property
    def info(self):
        return self.args[1]

    def __str__(self):
        return self.msg + ''.join('\n  {0}: {1}'.format(*i) for i in self.info.iteritems())
