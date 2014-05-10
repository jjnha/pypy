from rpython.rlib import rmpdec
from rpython.rlib.unroll import unrolling_iterable
from rpython.rtyper.lltypesystem import rffi, lltype
from pypy.interpreter.error import oefmt, OperationError
from pypy.interpreter.baseobjspace import W_Root
from pypy.interpreter.gateway import interp2app, unwrap_spec
from pypy.interpreter.typedef import (
    TypeDef, GetSetProperty, interp_attrproperty_w)
from pypy.interpreter.executioncontext import ExecutionContext
from pypy.module._decimal import interp_signals


# The SignalDict is a MutableMapping that provides access to the
# mpd_context_t flags, which reside in the context object.
# When a new context is created, context.traps and context.flags are
# initialized to new SignalDicts.
# Once a SignalDict is tied to a context, it cannot be deleted.
class W_SignalDictMixin(W_Root):
    def __init__(self, flag_ptr):
        self.flag_ptr = flag_ptr

    def descr_getitem(self, space, w_key):
        flag = interp_signals.exception_as_flag(space, w_key)
        cur_flag = rffi.cast(lltype.Signed, self.flag_ptr[0])
        return space.wrap(bool(flag & cur_flag))

    def descr_setitem(self, space, w_key, w_value):
        flag = interp_signals.exception_as_flag(space, w_key)
        cur_flag = rffi.cast(lltype.Signed, self.flag_ptr[0])
        if space.is_true(w_value):
            self.flag_ptr[0] = rffi.cast(rffi.UINT, cur_flag | flag)
        else:
            self.flag_ptr[0] = rffi.cast(rffi.UINT, cur_flag & ~flag)


def new_signal_dict(space, flag_ptr):
    w_dict = space.allocate_instance(W_SignalDictMixin,
                                     state_get(space).W_SignalDict)
    W_SignalDictMixin.__init__(w_dict, flag_ptr)
    return w_dict


W_SignalDictMixin.typedef = TypeDef(
    'SignalDictMixin',
    __getitem__ = interp2app(W_SignalDictMixin.descr_getitem),
    __setitem__ = interp2app(W_SignalDictMixin.descr_setitem),
    )
W_SignalDictMixin.typedef.acceptable_as_base_class = True


class State:
    def __init__(self, space):
        w_import = space.builtin.get('__import__')
        w_collections = space.call_function(w_import,
                                            space.wrap('collections'))
        w_MutableMapping = space.getattr(w_collections,
                                         space.wrap('MutableMapping'))
        self.W_SignalDict = space.call_function(
            space.w_type, space.wrap("SignalDict"),
            space.newtuple([space.gettypeobject(W_SignalDictMixin.typedef),
                            w_MutableMapping]),
            space.newdict())

def state_get(space):
    return space.fromcache(State)

ROUND_CONSTANTS = unrolling_iterable([
        (name, getattr(rmpdec, 'MPD_' + name))
        for name in rmpdec.ROUND_CONSTANTS])
DEC_DFLT_EMAX = 999999
DEC_DFLT_EMIN = -999999

class W_Context(W_Root):
    def __init__(self, space):
        self.ctx = lltype.malloc(rmpdec.MPD_CONTEXT_PTR.TO, flavor='raw',
                                 zero=True,
                                 track_allocation=False)
        # Default context
        self.ctx.c_prec = 28
        self.ctx.c_emax = DEC_DFLT_EMAX
        self.ctx.c_emin = DEC_DFLT_EMIN
        rffi.setintfield(self.ctx, 'c_traps',
                         (rmpdec.MPD_IEEE_Invalid_operation|
                          rmpdec.MPD_Division_by_zero|
                          rmpdec.MPD_Overflow))
        rffi.setintfield(self.ctx, 'c_status', 0)
        rffi.setintfield(self.ctx, 'c_newtrap', 0)
        rffi.setintfield(self.ctx, 'c_round', rmpdec.MPD_ROUND_HALF_EVEN)
        rffi.setintfield(self.ctx, 'c_clamp', 0)
        rffi.setintfield(self.ctx, 'c_allcr', 1)
        
        self.w_flags = new_signal_dict(
            space, lltype.direct_fieldptr(self.ctx, 'c_status'))
        self.w_traps = new_signal_dict(
            space, lltype.direct_fieldptr(self.ctx, 'c_traps'))
        self.capitals = 1

    def __del__(self):
        if self.ctx:
            lltype.free(self.ctx, flavor='raw', track_allocation=False)

    def addstatus(self, space, status):
        "Add resulting status to context, and eventually raise an exception."
        new_status = (rffi.cast(lltype.Signed, status) |
                      rffi.cast(lltype.Signed, self.ctx.c_status))
        self.ctx.c_status = rffi.cast(rffi.UINT, new_status)
        if new_status & rmpdec.MPD_Malloc_error:
            raise OperationError(space.w_MemoryError, space.w_None)
        to_trap = (rffi.cast(lltype.Signed, status) &
                   rffi.cast(lltype.Signed, self.ctx.c_traps))
        if to_trap:
            raise interp_signals.flags_as_exception(space, to_trap)

    def copy_w(self, space):
        w_copy = W_Context(space)
        rffi.structcopy(w_copy.ctx, self.ctx)
        w_copy.capitals = self.capitals
        return w_copy

    def get_prec(self, space):
        return space.wrap(rmpdec.mpd_getprec(self.ctx))

    def set_prec(self, space, w_prec):
        prec = space.int_w(w_prec)
        if not rmpdec.mpd_qsetprec(self.ctx, prec):
            raise oefmt(space.w_ValueError,
                        "valid range for prec is [1, MAX_PREC]")

    def get_rounding(self, space):
        return space.wrap(rmpdec.mpd_getround(self.ctx))

    def set_rounding(self, space, w_rounding):
        rounding = space.str_w(w_rounding)
        for name, value in ROUND_CONSTANTS:
            if name == rounding:
                break
        else:
            raise oefmt(space.w_TypeError,
                        "valid values for rounding are: "
                        "[ROUND_CEILING, ROUND_FLOOR, ROUND_UP, ROUND_DOWN,"
                        "ROUND_HALF_UP, ROUND_HALF_DOWN, ROUND_HALF_EVEN,"
                        "ROUND_05UP]")
        if not rmpdec.mpd_qsetround(self.ctx, value):
            raise oefmt(space.w_RuntimeError,
                        "internal error in context.set_rounding")

    def get_emin(self, space):
        return space.wrap(rmpdec.mpd_getemin(self.ctx))

    def set_emin(self, space, w_emin):
        emin = space.int_w(w_emin)
        if not rmpdec.mpd_qsetemin(self.ctx, emin):
            raise oefmt(space.w_ValueError,
                        "valid range for Emin is [MIN_EMIN, 0]")

    def get_emax(self, space):
        return space.wrap(rmpdec.mpd_getemax(self.ctx))

    def set_emax(self, space, w_emax):
        emax = space.int_w(w_emax)
        if not rmpdec.mpd_qsetemax(self.ctx, emax):
            raise oefmt(space.w_ValueError,
                        "valid range for Emax is [0, MAX_EMAX]")

    def get_clamp(self, space):
        return space.wrap(rmpdec.mpd_getclamp(self.ctx))

    def set_clamp(self, space, w_clamp):
        clamp = space.c_int_w(w_clamp)
        if not rmpdec.mpd_qsetclamp(self.ctx, clamp):
            raise oefmt(space.w_ValueError,
                        "valid values for clamp are 0 or 1")

    def create_decimal_w(self, space, w_value=None):
        from pypy.module._decimal import interp_decimal
        return interp_decimal.decimal_from_object(
            space, None, w_value, self, exact=False)


def descr_new_context(space, w_subtype, __args__):
    w_result = space.allocate_instance(W_Context, w_subtype)
    W_Context.__init__(w_result, space)
    return w_result

W_Context.typedef = TypeDef(
    'Context',
    __new__ = interp2app(descr_new_context),
    # Attributes
    flags=interp_attrproperty_w('w_flags', W_Context),
    traps=interp_attrproperty_w('w_traps', W_Context),
    prec=GetSetProperty(W_Context.get_prec, W_Context.set_prec),
    rounding=GetSetProperty(W_Context.get_rounding, W_Context.set_rounding),
    Emin=GetSetProperty(W_Context.get_emin, W_Context.set_emin),
    Emax=GetSetProperty(W_Context.get_emax, W_Context.set_emax),
    clamp=GetSetProperty(W_Context.get_clamp, W_Context.set_clamp),
    #
    copy=interp2app(W_Context.copy_w),
    create_decimal=interp2app(W_Context.create_decimal_w),
    )


ExecutionContext.decimal_context = None

def getcontext(space):
    ec = space.getexecutioncontext()
    if not ec.decimal_context:
        # Set up a new thread local context
        ec.decimal_context = W_Context(space)
    return ec.decimal_context

def setcontext(space, w_context):
    ec = space.getexecutioncontext()
    ec.decimal_context = space.interp_w(W_Context, w_context)

def ensure_context(space, w_context):
    context = space.interp_w(W_Context, w_context,
                             can_be_None=True)
    if context is None:
        context = getcontext(space)
    return context

class ConvContext:
    def __init__(self, space, mpd, context, exact):
        self.space = space
        self.mpd = mpd
        self.context = context
        self.exact = exact

    def __enter__(self):
        if self.exact:
            self.ctx = lltype.malloc(rmpdec.MPD_CONTEXT_PTR.TO, flavor='raw',
                                     zero=True)
            rmpdec.mpd_maxcontext(self.ctx)
        else:
            self.ctx = self.context.ctx
        self.status_ptr = lltype.malloc(rffi.CArrayPtr(rffi.UINT).TO, 1,
                                        flavor='raw', zero=True)
        return self.ctx, self.status_ptr

    def __exit__(self, *args):
        if self.exact:
            lltype.free(self.ctx, flavor='raw')
            # we want exact results
            status = rffi.cast(lltype.Signed, self.status_ptr[0])
            if status & (rmpdec.MPD_Inexact |
                         rmpdec.MPD_Rounded |
                         rmpdec.MPD_Clamped):
                rmpdec.mpd_seterror(
                    self.mpd, rmpdec.MPD_Invalid_operation, self.status_ptr)
        status = rffi.cast(lltype.Signed, self.status_ptr[0])
        lltype.free(self.status_ptr, flavor='raw')
        if self.exact:
            status &= rmpdec.MPD_Errors
        # May raise a DecimalException
        self.context.addstatus(self.space, status)