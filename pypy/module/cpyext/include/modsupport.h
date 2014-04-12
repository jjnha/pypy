
/* Module support interface */

#ifndef Py_MODSUPPORT_H
#define Py_MODSUPPORT_H
#ifdef __cplusplus
extern "C" {
#endif

/* If PY_SSIZE_T_CLEAN is defined, each functions treats #-specifier
   to mean Py_ssize_t */
#ifdef PY_SSIZE_T_CLEAN
#undef PyArg_Parse
#undef PyArg_ParseTuple
#undef PyArg_ParseTupleAndKeywords
#undef PyArg_VaParse
#undef PyArg_VaParseTupleAndKeywords
#undef Py_BuildValue
#undef Py_VaBuildValue
#define PyArg_Parse         _PyArg_Parse_SizeT
#define PyArg_ParseTuple        _PyArg_ParseTuple_SizeT
#define PyArg_ParseTupleAndKeywords _PyArg_ParseTupleAndKeywords_SizeT
#define PyArg_VaParse           _PyArg_VaParse_SizeT
#define PyArg_VaParseTupleAndKeywords   _PyArg_VaParseTupleAndKeywords_SizeT
#define Py_BuildValue           _Py_BuildValue_SizeT
#define Py_VaBuildValue         _Py_VaBuildValue_SizeT
#endif

#define Py_CLEANUP_SUPPORTED 0x20000

#define PYTHON_API_VERSION 1013
#define PYTHON_API_STRING "1013"
/* The PYTHON_ABI_VERSION is introduced in PEP 384. For the lifetime of
   Python 3, it will stay at the value of 3; changes to the limited API
   must be performed in a strictly backwards-compatible manner. */
#define PYTHON_ABI_VERSION 3
#define PYTHON_ABI_STRING "3"

int PyArg_Parse(PyObject *, const char *, ...);
int PyArg_ParseTuple(PyObject *, const char *, ...);
int PyArg_VaParse(PyObject *, const char *, va_list);

int PyArg_ParseTupleAndKeywords(PyObject *, PyObject *,
				const char *, char **, ...);
int PyArg_VaParseTupleAndKeywords(PyObject *, PyObject *,
				const char *, char **, va_list);

int _PyArg_Parse_SizeT(PyObject *, const char *, ...);
int _PyArg_ParseTuple_SizeT(PyObject *, const char *, ...);
int _PyArg_VaParse_SizeT(PyObject *, const char *, va_list);

int _PyArg_ParseTupleAndKeywords_SizeT(PyObject *, PyObject *,
				const char *, char **, ...);
int _PyArg_VaParseTupleAndKeywords_SizeT(PyObject *, PyObject *,
				const char *, char **, va_list);
  
PyAPI_FUNC(PyObject *) PyModule_Create2(struct PyModuleDef*,
					int apiver);
#ifdef Py_LIMITED_API
#define PyModule_Create(module) \
	PyModule_Create2(module, PYTHON_ABI_VERSION)
#else
#define PyModule_Create(module) \
	PyModule_Create2(module, PYTHON_API_VERSION)
#endif

int PyModule_AddObject(PyObject *m, const char *name, PyObject *o);
int PyModule_AddIntConstant(PyObject *m, const char *name, long value);
int PyModule_AddStringConstant(PyObject *m, const char *name, const char *value);
#define PyModule_AddIntMacro(m, c) PyModule_AddIntConstant(m, #c, c)
#define PyModule_AddStringMacro(m, c) PyModule_AddStringConstant(m, #c, c)


PyObject * Py_BuildValue(const char *, ...);
PyObject * Py_VaBuildValue(const char *, va_list);
PyObject * _Py_BuildValue_SizeT(const char *, ...);
PyObject * _Py_VaBuildValue_SizeT(const char *, va_list);
int _PyArg_NoKeywords(const char *funcname, PyObject *kw);

int PyArg_UnpackTuple(PyObject *args, const char *name, Py_ssize_t min, Py_ssize_t max, ...);

/*
 * This is from pyport.h.  Perhaps it belongs elsewhere.
 */
#ifdef __cplusplus
#define PyMODINIT_FUNC extern "C" PyObject*
#else
#define PyMODINIT_FUNC PyObject*
#endif

PyAPI_DATA(char *) _Py_PackageContext;

#ifdef __cplusplus
}
#endif
#endif /* !Py_MODSUPPORT_H */