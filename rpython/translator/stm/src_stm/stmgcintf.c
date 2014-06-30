/************************************************************/
/* This is not meant to be compiled stand-alone, but with all
   of PyPy's #defines and #includes prepended. */

__thread struct stm_thread_local_s stm_thread_local __attribute__((aligned(64)));

/* 0 = not initialized; 1 = normal mode; 2 or more = atomic mode */
__thread long pypy_stm_ready_atomic;
__thread uintptr_t pypy_stm_nursery_low_fill_mark;
__thread uintptr_t pypy_stm_nursery_low_fill_mark_saved;

extern Signed pypy_stmcb_size_rounded_up(void*);
extern void pypy_stmcb_get_card_base_itemsize(void*, uintptr_t[]);
extern void pypy_stmcb_trace(void*, void(*)(void*));
extern void pypy_stmcb_trace_cards(void*, void(*)(void*), uintptr_t, uintptr_t);

inline ssize_t stmcb_size_rounded_up(struct object_s *obj) {
    ssize_t result = pypy_stmcb_size_rounded_up(obj);
    OPT_ASSERT(result >= 16);
    OPT_ASSERT((result & 7) == 0);
    return result;
}

inline void stmcb_get_card_base_itemsize(struct object_s *obj,
                                         uintptr_t offset_itemsize[2]) {
    pypy_stmcb_get_card_base_itemsize(obj, offset_itemsize);
}

inline void stmcb_trace(struct object_s *obj, void visit(object_t **)) {
    pypy_stmcb_trace(obj, (void(*)(void*))visit);
}

inline void stmcb_trace_cards(struct object_s *obj, void visit(object_t **),
                              uintptr_t start, uintptr_t stop) {
    pypy_stmcb_trace_cards(obj, (void(*)(void*))visit, start, stop);
}

inline void stmcb_commit_soon()
{
    if (pypy_stm_nursery_low_fill_mark == (uintptr_t)-1) {
        /* atomic */
        if (((long)pypy_stm_nursery_low_fill_mark_saved) > 0) {
            pypy_stm_nursery_low_fill_mark_saved = 0;
        }
    } else if (((long)pypy_stm_nursery_low_fill_mark) > 0) {
        /* if not set to unlimited by pypy_stm_setup() (s.b.) */
        pypy_stm_nursery_low_fill_mark = 0;
    }
}


/************************************************************/
/* "include" the stmgc.c file here */
#include "src_stm/stmgc.c"

/************************************************************/


#define LOW_FILL_MARK   400000

static long pypy_transaction_length;


void pypy_stm_set_transaction_length(double fraction)
{
    /* the value '1.0' means 'use the default'.  Other values are
       interpreted proportionally, up to some maximum. */
    long low_fill_mark = (long)(LOW_FILL_MARK * fraction);
    if (low_fill_mark > (long)(NURSERY_SIZE * 3 / 4))
        low_fill_mark = NURSERY_SIZE * 3 / 4;
    pypy_transaction_length = low_fill_mark;
}

void pypy_stm_setup(void)
{
    stm_setup();
    pypy_stm_register_thread_local();
    pypy_stm_ready_atomic = 1;
    /* set transaction length to unlimited until the first thread
       starts. pypy_stm_set_transaction_length will then be called
       again by pypy. */
    pypy_stm_set_transaction_length(-10000.0);
    pypy_stm_start_inevitable_if_not_atomic();
}

void pypy_stm_teardown(void)
{
    pypy_stm_unregister_thread_local();
    /* stm_teardown() not called here for now; it's mostly for tests */
}

long pypy_stm_enter_callback_call(void)
{
    if (pypy_stm_ready_atomic == 0) {
        /* first time we see this thread */
        assert(pypy_transaction_length >= 0);
        int e = errno;
        pypy_stm_register_thread_local();
        errno = e;
        pypy_stm_ready_atomic = 1;
        pypy_stm_start_inevitable_if_not_atomic();
        return 1;
    }
    else {
        /* callback from C code, itself called from Python code */
        pypy_stm_start_inevitable_if_not_atomic();
        return 0;
    }
}

void pypy_stm_leave_callback_call(long token)
{
    if (token == 1) {
        /* if we're returning into foreign C code that was not itself
           called from Python code, then we're ignoring the atomic
           status and committing anyway. */
        int e = errno;
        pypy_stm_ready_atomic = 1;
        stm_commit_transaction();
        pypy_stm_ready_atomic = 0;
        pypy_stm_unregister_thread_local();
        errno = e;
    }
    else {
        pypy_stm_commit_if_not_atomic();
    }
}

void _pypy_stm_initialize_nursery_low_fill_mark(long v_counter)
{
    /* If v_counter==0, initialize 'pypy_stm_nursery_low_fill_mark'
       from the configured length limit.  If v_counter>0, we did an
       abort, and we now configure 'pypy_stm_nursery_low_fill_mark'
       to a value slightly smaller than the value at last abort.
    */
    long counter, limit;
#ifdef HTM_INFO_AVAILABLE
    if (_htm_info.use_gil)
        counter = 0;            /* maybe we want the default size here... */
    else
        counter = _htm_info.retry_counter;
    limit = pypy_transaction_length >> counter;
#else
    counter = v_counter;

    if (counter == 0) {
        limit = pypy_transaction_length;
    }
    else {
        limit = stm_thread_local.last_abort__bytes_in_nursery;
        limit -= (limit >> 4);
    }
#endif

    pypy_stm_nursery_low_fill_mark = _stm_nursery_start + limit;
}

void pypy_stm_start_transaction(stm_jmpbuf_t *jmpbuf_ptr,
                                volatile long *v_counter)
{
    pypy_stm_nursery_low_fill_mark = 1;  /* will be set to a correct value below */
    _stm_start_transaction(&stm_thread_local, jmpbuf_ptr);

    _pypy_stm_initialize_nursery_low_fill_mark(*v_counter);
    *v_counter = *v_counter + 1;

    pypy_stm_ready_atomic = 1; /* reset after abort */
}

void pypy_stm_perform_transaction(object_t *arg, int callback(object_t *, int))
{   /* must save roots around this call */
    stm_jmpbuf_t jmpbuf;
    long volatile v_counter = 0;
    int (*volatile v_callback)(object_t *, int) = callback;
#ifndef NDEBUG
    struct stm_shadowentry_s *volatile v_old_shadowstack =
        stm_thread_local.shadowstack;
#endif

    //STM_PUSH_ROOT(stm_thread_local, STM_STACK_MARKER_NEW);
    STM_PUSH_ROOT(stm_thread_local, arg);

    while (1) {
        long counter;

        if (pypy_stm_should_break_transaction()) { //pypy_stm_ready_atomic == 1) {
            /* Not in an atomic transaction; but it might be an inevitable
               transaction.
             */
            assert(pypy_stm_nursery_low_fill_mark != (uintptr_t) -1);

            stm_commit_transaction();

            /* After setjmp(), the local variables v_* are preserved because
               they are volatile.  The other local variables should be
               declared below than this point only.
            */
            while (__builtin_setjmp(jmpbuf) == 1) { /*redo setjmp*/ }
            counter = v_counter;
            pypy_stm_start_transaction(&jmpbuf, &v_counter);
        }
        else {
            /* In an atomic transaction */
            //assert(pypy_stm_nursery_low_fill_mark == (uintptr_t) -1);
            counter = v_counter;
        }

        /* invoke the callback in the new transaction */
        STM_POP_ROOT(stm_thread_local, arg);
        assert(v_old_shadowstack == stm_thread_local.shadowstack);// - 1);
        STM_PUSH_ROOT(stm_thread_local, arg);

        long result = v_callback(arg, counter);
        if (result <= 0)
            break;
        v_counter = 0;
    }

    if (STM_SEGMENT->jmpbuf_ptr == &jmpbuf) {
        /* we can't leave this function leaving a non-inevitable
           transaction whose jmpbuf points into this function.
           we could break the transaction here but we instead rely
           on the caller to break it. Since we have to use an inevitable
           transaction anyway, using the current one may be cheaper.
        */
        _stm_become_inevitable("perform_transaction left with inevitable");
    }
    /* double-check */
    if (pypy_stm_ready_atomic == 1) {
    }
    else {
        assert(pypy_stm_nursery_low_fill_mark == (uintptr_t) -1);
    }

    STM_POP_ROOT_RET(stm_thread_local);             /* pop the 'arg' */
    //uintptr_t x = (uintptr_t)STM_POP_ROOT_RET(stm_thread_local);
    //assert(x == STM_STACK_MARKER_NEW || x == STM_STACK_MARKER_OLD);
    assert(v_old_shadowstack == stm_thread_local.shadowstack);
}

void _pypy_stm_inev_state(void)
{
    /* Reduce the limit so that inevitable transactions are generally
       shorter. We depend a bit on stmcb_commit_soon() in order for
       other transactions to signal us in case we block them. */
    long t;
    if (pypy_stm_ready_atomic == 1) {
        t = (long)pypy_stm_nursery_low_fill_mark;
        t = _stm_nursery_start + ((t - (long)_stm_nursery_start) >> 2);
        pypy_stm_nursery_low_fill_mark = t;
    }
    else {
        assert(pypy_stm_nursery_low_fill_mark == (uintptr_t) -1);
        t = (long)pypy_stm_nursery_low_fill_mark_saved;
        t = _stm_nursery_start + ((t - (long)_stm_nursery_start) >> 2);
        pypy_stm_nursery_low_fill_mark_saved = t;
    }
}

void _pypy_stm_become_inevitable(const char *msg)
{
    _pypy_stm_inev_state();
    if (msg == NULL) {
        msg = "return from JITted function";
    }
    _stm_become_inevitable(msg);
}

void pypy_stm_become_globally_unique_transaction(void)
{
    if (STM_SEGMENT->jmpbuf_ptr != NULL) {
        _pypy_stm_inev_state();
    }
    stm_become_globally_unique_transaction(&stm_thread_local, "for the JIT");
}