from inspect import CO_VARARGS, CO_VARKEYWORDS

import py
from pypy.interpreter import gateway, pycode
from pypy.interpreter.error import OperationError

try:
    from _pytest.assertion.newinterpret import interpret
except ImportError:
    from _pytest.assertion.oldinterpret import interpret

# ____________________________________________________________

class AppCode(object):
    def __init__(self, space, pycode):
        self.code = pycode
        self.raw = pycode
        self.w_file = space.getattr(pycode, space.wrap('co_filename'))
        self.name = space.getattr(pycode, space.wrap('co_name'))
        self.firstlineno = space.unwrap(
            space.getattr(pycode, space.wrap('co_firstlineno'))) - 1
        #try:
        #    self.path = space.unwrap(space.getattr(self.w_file, space.wrap('__path__')))
        #except OperationError:
        #    self.path = space.unwrap(space.getattr(
        self.path = py.path.local(space.str_w(self.w_file))
        self.space = space
    
    def fullsource(self):
        filename = self.space.str_w(self.w_file)
        source = py.code.Source(py.std.linecache.getlines(filename))
        if source.lines:
            return source
        try:
            return py.code.Source(self.path.read(mode="rU"))
        except py.error.Error:
            return None
    fullsource = property(fullsource, None, None, "Full source of AppCode")

    def getargs(self, var=False):
        raw = self.raw
        argcount = raw.co_argcount
        if var:
            argcount += raw.co_flags & CO_VARARGS
            argcount += raw.co_flags & CO_VARKEYWORDS
        return raw.co_varnames[:argcount]

class AppFrame(py.code.Frame):

    def __init__(self, space, pyframe):
        self.code = AppCode(space, \
            space.unwrap(space.getattr(pyframe, space.wrap('f_code'))))
        #self.code = py.code.Code(pyframe.pycode)
        self.lineno = space.unwrap(space.getattr(pyframe, space.wrap('f_lineno'))) - 1
        #pyframe.get_last_lineno() - 1
        self.space = space
        self.w_globals = space.getattr(pyframe, space.wrap('f_globals'))
        self.w_locals = space.getattr(pyframe, space.wrap('f_locals'))
        self.f_locals = self.w_locals   # for py.test's recursion detection

    def eval(self, code, **vars):
        space = self.space
        for key, w_value in vars.items():
            space.setitem(self.w_locals, space.wrap(key), w_value)
        if isinstance(code, str):
            return space.eval(code, self.w_globals, self.w_locals)
        pyc = pycode.PyCode._from_code(space, code)
        return pyc.exec_host_bytecode(self.w_globals, self.w_locals)
    exec_ = eval

    def repr(self, w_value):
        try:
            return self.space.unwrap(self.space.repr(w_value))
        except Exception, e:
            return "<Sorry, exception while trying to do repr, %r>"%e

    def is_true(self, w_value):
        return self.space.is_true(w_value)

    def getargs(self, var=False):
        space = self.space
        retval = []
        for arg in self.code.getargs(var):
            w_val = space.finditem(self.w_locals, space.wrap(arg))
            if w_val is None:
                w_val = space.wrap('<no value found>')
            retval.append((arg, w_val))
        return retval


class AppExceptionInfo(py.code.ExceptionInfo):
    """An ExceptionInfo object representing an app-level exception."""

    def __init__(self, space, operr):
        self.space = space
        self.operr = operr
        self.typename = operr.w_type.getname(space).encode('utf-8')
        self.traceback = AppTraceback(space, self.operr.get_traceback())
        debug_excs = getattr(operr, 'debug_excs', [])
        if debug_excs:
            self._excinfo = debug_excs[0]

    def __repr__(self):
        return "<AppExceptionInfo %s>" % self.operr.errorstr(self.space)

    def exconly(self, tryshort=True):
        return '(application-level) ' + self.operr.errorstr(self.space)

    def errisinstance(self, exc): 
        clsname = exc.__name__ 
        # we can only check for builtin exceptions
        # as there is no canonical applevel one for custom interplevel ones
        if exc.__module__ != "exceptions":
            return False 
        try: 
            w_exc = getattr(self.space, 'w_' + clsname) 
        except KeyboardInterrupt: 
            raise 
        except: 
            pass 
        else: 
            return self.operr.match(self.space, w_exc) 
        return False 

    def __str__(self):
        return '(application-level) ' + self.operr.errorstr(self.space)

class AppTracebackEntry(py.code.Traceback.Entry):
    exprinfo = None

    def __init__(self, space, tb):
        self.frame = AppFrame(space, space.getattr(tb, space.wrap('tb_frame')))
        self.lineno = space.unwrap(space.getattr(tb, space.wrap('tb_lineno'))) - 1

    def reinterpret(self):
        # XXX we need to solve a general problem: how to prevent
        #     reinterpretation from generating a different exception?
        #     This problem includes the fact that exprinfo will generate
        #     its own long message that looks like
        #        OperationError:   << [<W_TypeObject(NameError)>: W_StringObj...
        #     which is much less nice than the one produced by str(self).
        # XXX this reinterpret() is only here to prevent reinterpretation.
        return self.exprinfo

class AppTraceback(py.code.Traceback): 
    Entry = AppTracebackEntry 

    def __init__(self, space, apptb):
        l = []
        while apptb is not space.w_None and apptb is not None:
            l.append(self.Entry(space, apptb))
            apptb = space.getattr(apptb, space.wrap('tb_next'))
        list.__init__(self, l)
    
# ____________________________________________________________

def build_pytest_assertion(space):
    def my_init(space, w_self, __args__):
        "Our new AssertionError.__init__()."
        w_parent_init = space.getattr(w_BuiltinAssertionError,
                                      space.wrap('__init__'))
        space.call_args(w_parent_init, __args__.prepend(w_self))
##        # Argh! we may see app-level helpers in the frame stack!
##        #       that's very probably very bad...
##        ^^^the above comment may be outdated, but we are not sure
        
        # if the assertion provided a message, don't do magic
        args_w, kwargs_w = __args__.unpack()
        if args_w: 
            w_msg = args_w[0]
        else:
            frame = space.getexecutioncontext().gettopframe()
            runner = AppFrame(space, frame)
            try:
                source = runner.statement
                source = str(source).strip()
            except py.error.ENOENT: 
                source = None
            from pypy import conftest
            if source and py.test.config._assertstate.mode != "off":
                msg = interpret(source, runner, should_fail=True)
                space.setattr(w_self, space.wrap('args'),
                            space.newtuple([space.wrap(msg)]))
                w_msg = space.wrap(msg)
            else:
                w_msg = space.w_None
        space.setattr(w_self, space.wrap('msg'), w_msg)

    # build a new AssertionError class to replace the original one.
    w_BuiltinAssertionError = space.getitem(space.builtin.w_dict, 
                                            space.wrap('AssertionError'))
    w_metaclass = space.type(w_BuiltinAssertionError)
    w_init = space.wrap(gateway.interp2app_temp(my_init))
    w_dict = space.getattr(w_BuiltinAssertionError, space.wrap('__dict__'))
    w_dict = space.call_method(w_dict, 'copy')
    space.setitem(w_dict, space.wrap('__init__'), w_init)
    return space.call_function(w_metaclass,
                               space.wrap('AssertionError'),
                               space.newtuple([w_BuiltinAssertionError]),
                               w_dict)

def _exc_info(space, err):
    """Hack the fact that exc_info() isn't set until a app except
    block catches it."""
    err.normalize_exception(space)
    frame = space.getexecutioncontext().gettopframe()
    old = frame.last_exception
    frame.last_exception = err
    if not hasattr(space, '_w_ExceptionInfo'):
        space._w_ExceptionInfo = space.appexec([], """():
    class _ExceptionInfo(object):
        def __init__(self):
            import sys
            self.type, self.value, self.traceback = sys.exc_info()

    return _ExceptionInfo
""")
    try:
        return space.call_function(space._w_ExceptionInfo)
    finally:
        frame.last_exception = old

def pypyraises(space, w_ExpectedException, w_expr, __args__):
    """A built-in function providing the equivalent of py.test.raises()."""
    args_w, kwds_w = __args__.unpack()
    if space.isinstance_w(w_expr, space.w_unicode):
        if args_w:
            raise OperationError(space.w_TypeError,
                                 space.wrap("raises() takes no argument "
                                            "after a string expression"))
        expr = space.unwrap(w_expr)
        source = py.code.Source(expr)
        frame = space.getexecutioncontext().gettopframe()
        w_locals = frame.getdictscope()
        pycode = frame.pycode
        filename = "<%s:%s>" %(pycode.co_filename, frame.f_lineno)
        lines = [x + "\n" for x in expr.split("\n")]
        py.std.linecache.cache[filename] = (1, None, lines, filename)
        w_locals = space.call_method(w_locals, 'copy')
        for key, w_value in kwds_w.items():
            space.setitem(w_locals, space.wrap(key), w_value)
        #filename = __file__
        #if filename.endswith("pyc"):
        #    filename = filename[:-1]
        try:
            space.exec_(unicode(source).encode('utf-8'), frame.w_globals, w_locals,
                        filename=filename)
        except OperationError, e:
            if e.match(space, w_ExpectedException):
                return _exc_info(space, e)
            raise
    else:
        try:
            space.call_args(w_expr, __args__)
        except OperationError, e:
            if e.match(space, w_ExpectedException):
                return _exc_info(space, e)
            raise
    raise OperationError(space.w_AssertionError,
                         space.wrap("DID NOT RAISE"))

app_raises = gateway.interp2app_temp(pypyraises)

def pypyskip(space, w_message): 
    """skip a test at app-level. """ 
    msg = space.unwrap(w_message) 
    py.test.skip(msg)

app_skip = gateway.interp2app_temp(pypyskip)

def py3k_pypyskip(space, w_message): 
    """skip a test at app-level. """ 
    msg = space.unwrap(w_message) 
    py.test.skip('[py3k] %s' % msg)

app_py3k_skip = gateway.interp2app_temp(py3k_pypyskip)

def raises_w(space, w_ExpectedException, *args, **kwds):
    try:
        excinfo = py.test.raises(OperationError, *args, **kwds)
        type, value, tb = excinfo._excinfo
        if not value.match(space, w_ExpectedException):
            raise type, value, tb
        return excinfo
    except py.test.raises.Exception, e:
        e.tbindex = getattr(e, 'tbindex', -1) - 1
        raise