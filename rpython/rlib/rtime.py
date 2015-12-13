"""
RPython implementations of time.time(), time.clock(), time.select().
"""

import sys
import math
import time as pytime
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rtyper.tool import rffi_platform
from rpython.rtyper.lltypesystem import rffi, lltype
from rpython.rlib.objectmodel import register_replacement_for
from rpython.rlib import jit
from rpython.rlib.rarithmetic import intmask, UINT_MAX
from rpython.rlib import rposix

_WIN32 = sys.platform.startswith('win')

if _WIN32:
    TIME_H = 'time.h'
    FTIME = '_ftime64'
    STRUCT_TIMEB = 'struct __timeb64'
    includes = ['winsock2.h', 'windows.h',
                TIME_H, 'sys/types.h', 'sys/timeb.h']
    need_rusage = False
else:
    TIME_H = 'sys/time.h'
    FTIME = 'ftime'
    STRUCT_TIMEB = 'struct timeb'
    includes = [TIME_H, 'time.h', 'errno.h', 'sys/select.h',
                'sys/types.h', 'unistd.h',
                'sys/time.h', 'sys/resource.h']

    if not sys.platform.startswith("openbsd"):
        includes.append('sys/timeb.h')

    need_rusage = True


eci = ExternalCompilationInfo(includes=includes)

class CConfig:
    _compilation_info_ = eci
    TIMEVAL = rffi_platform.Struct('struct timeval', [('tv_sec', rffi.INT),
                                                      ('tv_usec', rffi.INT)])
    HAVE_GETTIMEOFDAY = rffi_platform.Has('gettimeofday')
    HAVE_FTIME = rffi_platform.Has(FTIME)
    if need_rusage:
        RUSAGE = rffi_platform.Struct('struct rusage', [('ru_utime', TIMEVAL),
                                                        ('ru_stime', TIMEVAL)])

if sys.platform.startswith('freebsd') or sys.platform.startswith('netbsd'):
    libraries = ['compat']
elif sys.platform == 'linux2':
    libraries = ['rt']
else:
    libraries = []

class CConfigForFTime:
    _compilation_info_ = ExternalCompilationInfo(
        includes=[TIME_H, 'sys/timeb.h'],
        libraries=libraries
    )
    TIMEB = rffi_platform.Struct(STRUCT_TIMEB, [('time', rffi.INT),
                                                ('millitm', rffi.INT)])

class CConfigForClockGetTime:
    _compilation_info_ = ExternalCompilationInfo(
        includes=['time.h'],
        libraries=libraries
    )
    TIMESPEC = rffi_platform.Struct('struct timespec', [('tv_sec', rffi.LONG),
                                                        ('tv_nsec', rffi.LONG)])

constant_names = ['RUSAGE_SELF', 'EINTR', 'CLOCK_PROCESS_CPUTIME_ID']
for const in constant_names:
    setattr(CConfig, const, rffi_platform.DefinedConstantInteger(const))
defs_names = ['GETTIMEOFDAY_NO_TZ']
for const in defs_names:
    setattr(CConfig, const, rffi_platform.Defined(const))

def decode_timeval(t):
    return (float(rffi.getintfield(t, 'c_tv_sec')) +
            float(rffi.getintfield(t, 'c_tv_usec')) * 0.000001)


def external(name, args, result, compilation_info=eci, **kwds):
    return rffi.llexternal(name, args, result,
                           compilation_info=compilation_info, **kwds)

def replace_time_function(name):
    func = getattr(pytime, name, None)
    if func is None:
        return lambda f: f
    return register_replacement_for(
        func,
        sandboxed_name='ll_time.ll_time_%s' % name)

config = rffi_platform.configure(CConfig)
globals().update(config)

# Note: time.time() is used by the framework GC during collect(),
# which means that we have to be very careful about not allocating
# GC memory here.  This is the reason for the _nowrapper=True.
if HAVE_GETTIMEOFDAY:
    if GETTIMEOFDAY_NO_TZ:
        c_gettimeofday = external('gettimeofday',
                                  [lltype.Ptr(TIMEVAL)], rffi.INT,
                                  _nowrapper=True, releasegil=False)
    else:
        c_gettimeofday = external('gettimeofday',
                                  [lltype.Ptr(TIMEVAL), rffi.VOIDP], rffi.INT,
                                  _nowrapper=True, releasegil=False)
if HAVE_FTIME:
    globals().update(rffi_platform.configure(CConfigForFTime))
    c_ftime = external(FTIME, [lltype.Ptr(TIMEB)],
                         lltype.Void,
                         _nowrapper=True, releasegil=False)
c_time = external('time', [rffi.VOIDP], rffi.TIME_T,
                  _nowrapper=True, releasegil=False)


@replace_time_function('time')
def time():
    void = lltype.nullptr(rffi.VOIDP.TO)
    result = -1.0
    if HAVE_GETTIMEOFDAY:
        with lltype.scoped_alloc(TIMEVAL) as t:
            errcode = -1
            if GETTIMEOFDAY_NO_TZ:
                errcode = c_gettimeofday(t)
            else:
                errcode = c_gettimeofday(t, void)

            if rffi.cast(rffi.LONG, errcode) == 0:
                result = decode_timeval(t)
        if result != -1:
            return result
    else: # assume using ftime(3)
        with lltype.scoped_alloc(TIMEB) as t:
            c_ftime(t)
            result = (float(intmask(t.c_time)) +
                      float(intmask(t.c_millitm)) * 0.001)
        return result
    return float(c_time(void))


# _______________________________________________________________
# time.clock()

if _WIN32:
    # hacking to avoid LARGE_INTEGER which is a union...
    A = lltype.FixedSizeArray(lltype.SignedLongLong, 1)
    QueryPerformanceCounter = external(
        'QueryPerformanceCounter', [lltype.Ptr(A)], lltype.Void,
        releasegil=False)
    QueryPerformanceFrequency = external(
        'QueryPerformanceFrequency', [lltype.Ptr(A)], rffi.INT,
        releasegil=False)
    class State(object):
        divisor = 0.0
        counter_start = 0
    state = State()
elif CLOCK_PROCESS_CPUTIME_ID is not None:
    # Linux and other POSIX systems with clock_gettime()
    globals().update(rffi_platform.configure(CConfigForClockGetTime))
    TIMESPEC = TIMESPEC
    CLOCK_PROCESS_CPUTIME_ID = CLOCK_PROCESS_CPUTIME_ID
    c_clock_gettime = external('clock_gettime',
                               [lltype.Signed, lltype.Ptr(TIMESPEC)],
                               rffi.INT, releasegil=False)
else:
    RUSAGE = RUSAGE
    RUSAGE_SELF = RUSAGE_SELF or 0
    c_getrusage = external('getrusage',
                           [rffi.INT, lltype.Ptr(RUSAGE)],
                           lltype.Void,
                           releasegil=False)

@replace_time_function('clock')
@jit.dont_look_inside  # the JIT doesn't like FixedSizeArray
def clock():
    if _WIN32:
        a = lltype.malloc(A, flavor='raw')
        if state.divisor == 0.0:
            QueryPerformanceCounter(a)
            state.counter_start = a[0]
            QueryPerformanceFrequency(a)
            state.divisor = float(a[0])
        QueryPerformanceCounter(a)
        diff = a[0] - state.counter_start
        lltype.free(a, flavor='raw')
        return float(diff) / state.divisor
    elif CLOCK_PROCESS_CPUTIME_ID is not None:
        with lltype.scoped_alloc(TIMESPEC) as a:
            c_clock_gettime(CLOCK_PROCESS_CPUTIME_ID, a)
            result = (float(rffi.getintfield(a, 'c_tv_sec')) +
                      float(rffi.getintfield(a, 'c_tv_nsec')) * 0.000000001)
        return result
    else:
        with lltype.scoped_alloc(RUSAGE) as a:
            c_getrusage(RUSAGE_SELF, a)
            result = (decode_timeval(a.c_ru_utime) +
                      decode_timeval(a.c_ru_stime))
        return result

# _______________________________________________________________
# time.sleep()

if _WIN32:
    Sleep = external('Sleep', [rffi.ULONG], lltype.Void)
else:
    c_select = external('select', [rffi.INT, rffi.VOIDP,
                                   rffi.VOIDP, rffi.VOIDP,
                                   lltype.Ptr(TIMEVAL)], rffi.INT,
                        save_err=rffi.RFFI_SAVE_ERRNO)

@replace_time_function('sleep')
def sleep(secs):
    if _WIN32:
        millisecs = secs * 1000.0
        while millisecs > UINT_MAX:
            Sleep(UINT_MAX)
            millisecs -= UINT_MAX
        Sleep(rffi.cast(rffi.ULONG, int(millisecs)))
    else:
        void = lltype.nullptr(rffi.VOIDP.TO)
        with lltype.scoped_alloc(TIMEVAL) as t:
            frac = math.fmod(secs, 1.0)
            rffi.setintfield(t, 'c_tv_sec', int(secs))
            rffi.setintfield(t, 'c_tv_usec', int(frac*1000000.0))

            if rffi.cast(rffi.LONG, c_select(0, void, void, void, t)) != 0:
                errno = rposix.get_saved_errno()
                if errno != EINTR:
                    raise OSError(errno, "Select failed")