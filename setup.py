
from distutils.core import setup


# this is automatically updated by pre-commit (when installed as a git hook)
VERSION='0.0'

setup(
    name='PyExpose',
    version=VERSION,
    description='Generates a Python extension by inspecting your C++ classes and functions',
    url='https://github.com/Rouslan/PyExpose',
    author='Rouslan Korneychuk',
    author_email='rouslank@msn.com',
    packages=['pyexpose','pyexpose.test'],
    headers=['include/pyexpose_common.h','include/pyobject.h'],
    scripts=['scripts/pyexpose'],
    requires=['Jinja2'])
