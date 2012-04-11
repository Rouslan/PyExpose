#ifndef pyobject_h
#define pyobject_h

#include <algorithm>
#include <structmember.h>


#ifndef PYEXPOSE_TEMPLATE_HELPERS
    #ifdef pyexpose_common_h
        #error To use pyobject.h, PYEXPOSE_TEMPLATE_HELPERS must be defined before including pyexpose_common.h
    #endif
    #define PYEXPOSE_TEMPLATE_HELPERS
#endif
#include "pyexpose_common.h"



#pragma GCC visibility push(hidden)


#define THROW_PYERR_STRING(exception,message) { PyErr_SetString(PyExc_##exception,message); throw py_error_set(); }


namespace py {

    // Exception-safe alternative to Py_BEGIN_ALLOW_THREADS and Py_END_ALLOW_THREADS
    class AllowThreads {
#ifdef WITH_THREAD
        PyThreadState *save;
    public:
        AllowThreads() { save = PyEval_SaveThread(); }
        ~AllowThreads() { PyEval_RestoreThread(save); }
#endif
    };

    inline PyObject *check_obj(PyObject *o) {
        if(!o) throw py_error_set();
        return o;
    }

    inline PyObject *incref(PyObject *o) {
        assert(o);
        Py_INCREF(o);
        return o;
    }

    inline PyObject *xincref(PyObject *o) {
        Py_XINCREF(o);
        return o;
    }

    struct borrowed_ref {
        PyObject *_ptr;
        explicit borrowed_ref(PyObject *ptr) : _ptr(ptr) {}
    };

    struct new_ref {
        PyObject *_ptr;
        explicit new_ref(PyObject *ptr) : _ptr(ptr) {}
    };

    template<typename T> T *get_base_or_none(PyObject *o) {
        return o == Py_None ? NULL : &get_base<T>(o);
    }


    class object;
    class object_attr_proxy;
    class object_item_proxy;

    class _object_base {
    protected:
        PyObject *_ptr;

        void reset(PyObject *b) {
            Py_INCREF(b);

            // cyclic gargable collection safety
            PyObject *tmp = _ptr;
            _ptr = b;
            Py_DECREF(tmp);
        }

        _object_base(PyObject *ptr) : _ptr(ptr) {}
        _object_base(borrowed_ref r) : _ptr(incref(r._ptr)) {}
        _object_base(new_ref r) : _ptr(r._ptr) { assert(_ptr); }
        _object_base(const _object_base &b) : _ptr(incref(b._ptr)) {}

        ~_object_base() {
            Py_DECREF(_ptr);
        }

        void swap(_object_base &b) {
            PyObject *tmp = _ptr;
            _ptr = b._ptr;
            b._ptr = tmp;
        }

    public:
        operator bool() const {
            return PyObject_IsTrue(_ptr);
        }

        PyObject *get() const { return _ptr; }
        PyObject *get_new_ref() const { return incref(_ptr); }

        object_attr_proxy attr(const char *name) const;

        bool has_attr(const char *name) const { return PyObject_HasAttrString(_ptr,name); }
        bool has_attr(const _object_base &name) const { return PyObject_HasAttr(_ptr,name._ptr); }

        object operator()() const;
        template<typename A> object operator()(A a) const;
        template<typename A,typename B> object operator()(A a,B b) const;
        template<typename A,typename B,typename C> object operator()(A a,B b,C c) const;

#define OBJECT_OPERATOR(OP,PYOP) bool operator OP(const _object_base &b) const { return bool(PyObject_RichCompareBool(_ptr,b._ptr,PYOP)); }
        OBJECT_OPERATOR(==,Py_EQ)
        OBJECT_OPERATOR(!=,Py_NE)
        OBJECT_OPERATOR(<,Py_LT)
        OBJECT_OPERATOR(<=,Py_LE)
        OBJECT_OPERATOR(>,Py_GT)
        OBJECT_OPERATOR(>=,Py_GE)

        template<typename T> object_item_proxy at(T key) const;
        template<typename T> object_item_proxy operator[](T key) const;

        int __py_traverse__(visitproc visit,void *arg) const { return _ptr != Py_None ? (*visit)(_ptr,arg) : 0; }
        void __py_clear__() { reset(Py_None); }
        PyObject *__py_to_pyobject__() const { return incref(_ptr); }
    };

    class object : public _object_base {
    public:
        object(borrowed_ref r) : _object_base(r) {}
        object(new_ref r) : _object_base(r) {}
        object(const _object_base &b) : _object_base(b) {}

        object &operator=(const _object_base &b) {
            reset(b.get());
            return *this;
        }

        void swap(object &b) { _object_base::swap(b); }

        static object __py_from_pyobject__(PyObject *val) { return new_ref(val); }
    };

    template<typename T> inline object make_object(T x) {
        return object(new_ref(to_pyobject(x)));
    }

    class object_attr_proxy {
        friend class _object_base;
        friend void del(const object_attr_proxy &attr);

        PyObject *_ptr;
        const char *name;

        object_attr_proxy(PyObject *ptr,const char *name) : _ptr(ptr), name(name) {}
    public:
        operator object() const { return new_ref(check_obj(PyObject_GetAttrString(_ptr,name))); }

        object_attr_proxy &operator=(object val) {
            if(PyObject_SetAttrString(_ptr,name,val.get()) == -1) throw py_error_set();
            return *this;
        }

        // to make sure object_a.attr("x") = object_b.attr("y") works as expected
        object_attr_proxy &operator=(const object_attr_proxy &val) {
            return operator=(static_cast<object>(val));
        }

#ifdef PYOBJECT_USE_CXX0X
        template<typename... Args> object operator(Args... args) const {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(
                _ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(args).get()...,
                0)));
        }
#else
        object operator()() const {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),0)));
        }

        template<typename A> object operator()(A a) const {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(a).get(),
                0)));
        }

        template<typename A,typename B> object operator()(A a,B b) const {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(a).get(),
                make_object(b).get(),
                0)));
        }

        template<typename A,typename B,typename C> object operator()(A a,B b,C c) const {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(a).get(),
                make_object(b).get(),
                make_object(c).get(),
                0)));
        }
#endif
    };

    inline object_attr_proxy _object_base::attr(const char *name) const { return object_attr_proxy(_ptr,name); }

    inline object _object_base::operator()() const {
        return new_ref(check_obj(PyObject_CallObject(_ptr,0)));
    }

    template<typename A> inline object _object_base::operator()(A a) const {
        return new_ref(check_obj(PyObject_CallFunctionObjArgs(_ptr,
            make_object(a).get(),
            0)));
    }

    template<typename A,typename B> inline object _object_base::operator()(A a,B b) const {
        return new_ref(check_obj(PyObject_CallFunctionObjArgs(_ptr,
            make_object(a).get(),
            make_object(b).get(),
            0)));
    }

    template<typename A,typename B,typename C> inline object _object_base::operator()(A a,B b,C c) const {
        return new_ref(check_obj(PyObject_CallFunctionObjArgs(_ptr,
            make_object(a).get(),
            make_object(b).get(),
            make_object(c).get(),
            0)));
    }

    inline void del(const object_attr_proxy &attr) {
        if(PyObject_DelAttrString(attr._ptr,attr.name) == -1) throw py_error_set();
    }


    class object_item_proxy {
        friend class _object_base;
        friend void del(const object_item_proxy &item);

        PyObject *_ptr;
        PyObject *key;

        object_item_proxy(PyObject *ptr,PyObject * key) : _ptr(ptr), key(key) {}
    public:
        object_item_proxy(const object_item_proxy &b) : _ptr(b._ptr), key(incref(b.key)) {}
        ~object_item_proxy() {
            Py_DECREF(key);
        }

        operator object() const { return new_ref(check_obj(PyObject_GetItem(_ptr,key))); }

        object_item_proxy &operator=(object val) {
            if(PyObject_SetItem(_ptr,key,val.get()) == -1) throw py_error_set();
            return *this;
        }

        // so object_a[x] = object_b[y] works as expected
        object_item_proxy &operator=(const object_item_proxy &val) {
            return operator=(static_cast<object>(val));
        }
    };

    template<typename T> inline object_item_proxy _object_base::at(T key) const {
        return object_item_proxy(_ptr,to_pyobject(key));
    }

    template<typename T> inline object_item_proxy _object_base::operator[](T key) const {
        return at<T>(key);
    }

    inline void del(const object_item_proxy &item) {
        if(PyObject_DelItem(item._ptr,item.key) == -1) throw py_error_set();
    }


    template<typename T> class _nullable {
        PyObject *_ptr;

        void reset(PyObject *b) {
            // cyclic gargable collection safety
            PyObject *tmp = _ptr;
            _ptr = b;
            Py_XDECREF(tmp);
        }
    public:
        _nullable() : _ptr(NULL) {}
        _nullable(const _nullable<T> &b) : _ptr(incref(b._ptr)) {}
        _nullable(const T &b) : _ptr(b.get_new_ref()) {}

        _nullable<T> &operator=(const _nullable<T> &b) {
            Py_XINCREF(b._ptr);
            reset(b._ptr);
            return *this;
        }
        _nullable<T> &operator=(const T &b) {
            Py_INCREF(b._ptr);
            reset(b._ptr);
            return *this;
        }

        operator bool() const { return _ptr != NULL; }
        T operator*() const {
            assert(_ptr);
            return borrowed_ref(_ptr);
        }
        T operator->() const {
            assert(_ptr);
            return borrowed_ref(_ptr);
        }

        PyObject *get() const { return _ptr; }

        int __py_traverse__(visitproc visit,void *arg) const { return _ptr ? (*visit)(_ptr,arg) : 0; }
        void __py_clear__() { reset(NULL); }
        // __py_to_pyobject__ intentionally omitted
        static _nullable<T> __py_from_pyobject__(PyObject *val) { return T::__py_from_pyobject__(val); }
        static const int __py_cast_as_member_t__ = T_OBJECT_EX;
    };

    typedef _nullable<object> nullable_object;


#if PY_VERSION_HEX >= 0x02060000
    class BufferView {
        Py_buffer view;
    public:
        BufferView(PyObject *obj,int flags) {
            if(PyObject_GetBuffer(obj,&view,flags)) throw py_error_set();
        }

        BufferView(object obj,int flags) {
            if(PyObject_GetBuffer(obj.get(),&view,flags)) throw py_error_set();
        }

        ~BufferView() {
            PyBuffer_Release(&view);
        }

        void *buf() const { return view.buf; }
        Py_ssize_t len() const { return view.len; }
        int readonly() const { return view.readonly; }
        const char *format() const { return view.format; }
        int ndim() const { return view.ndim; }
        Py_ssize_t *shape() const { return view.shape; }
        Py_ssize_t *strides() const { return view.strides; }
        Py_ssize_t *suboffsets() const { return view.suboffsets; }
        Py_ssize_t itemsize() const { return view.itemsize; }
        void *internal() const { return view.internal; }
    };
#endif


    class tuple : public _object_base {
    public:
        tuple(borrowed_ref r) : _object_base(r) { assert(PyTuple_Check(r._ptr)); }
        tuple(new_ref r) : _object_base(r) { assert(PyTuple_Check(r._ptr)); }
        explicit tuple(Py_ssize_t len) : _object_base(new_ref(check_obj(PyTuple_New(len)))) {}
        tuple(const tuple &b) : _object_base(b) {}

        tuple &operator=(const tuple &b) {
            reset(b._ptr);
            return *this;
        }

        void swap(tuple &b) { _object_base::swap(b); }

        object at(Py_ssize_t i) const { return borrowed_ref(check_obj(PyTuple_GetItem(_ptr,i))); }
        void set_unsafe(Py_ssize_t i,PyObject *item) const { PyTuple_SET_ITEM(_ptr,i,item); }
        object operator[](Py_ssize_t i) const { return borrowed_ref(PyTuple_GET_ITEM(_ptr,i)); }
        Py_ssize_t size() const { return PyTuple_GET_SIZE(_ptr); }

        static tuple __py_from_pyobject__(PyObject *val) {
            if(!PyTuple_Check(val)) THROW_PYERR_STRING(TypeError,"object is not an instance of tuple")
            return new_ref(val);
        }
    };

    typedef _nullable<tuple> nullable_tuple;


    class dict_item_proxy {
        friend class dict;
        friend void del(const dict_item_proxy &item);

        PyObject *_ptr;
        PyObject *key;

        dict_item_proxy(PyObject *ptr,PyObject * key) : _ptr(ptr), key(key) {}
    public:
        dict_item_proxy(const dict_item_proxy &b) : _ptr(b._ptr), key(incref(b.key)) {}
        ~dict_item_proxy() {
            Py_DECREF(key);
        }

        operator object() const {
            /* using mp_subscript because it sets the error for us if the key
               isn't found */
            PyMappingMethods *m = _ptr->ob_type->tp_as_mapping;
            assert(m && m->mp_subscript);
            PyObject *item = (*m->mp_subscript)(_ptr,key);
            if(!item) throw py_error_set();
            return new_ref(item);
        }

        dict_item_proxy &operator=(object val) {
            if(PyDict_SetItem(_ptr,key,val.get()) == -1) throw py_error_set();
            return *this;
        }

        // so object_a[x] = object_b[y] works as expected
        dict_item_proxy &operator=(const dict_item_proxy &val) {
            return operator=(static_cast<object>(val));
        }
    };

    class dict : public _object_base {
    public:
        dict(borrowed_ref r) : _object_base(r) { assert(PyDict_Check(r._ptr)); }
        dict(new_ref r) : _object_base(r) { assert(PyDict_Check(r._ptr)); }
        dict() : _object_base(new_ref(check_obj(PyDict_New()))) {}
        dict(const dict &b) : _object_base(b) {}

        dict &operator=(const dict &b) {
            reset(b._ptr);
            return *this;
        }

        void swap(dict &b) { _object_base::swap(b); }

        template<typename T> dict_item_proxy operator[](T key) const { return dict_item_proxy(_ptr,to_pyobject(key)); }
        Py_ssize_t size() const { return PyDict_Size(_ptr); }
        template<typename T> nullable_object find(T key) const {
#if PY_MAJOR_VERSION >= 3
            PyObject *item = PyDict_GetItemWithError(_ptr,to_pyobject(key));
            if(!item && PyErr_Occurred()) throw py_error_set();
            return borrowed_ref(item);
#else
            /* mp_subscript is used instead of PyDict_GetItem because the latter
               swallows all errors */
            PyMappingMethods *m = _ptr->ob_type->tp_as_mapping;
            assert(m && m->mp_subscript);
            PyObject *item = (*m->mp_subscript)(_ptr,to_pyobject(key));
            if(!item) {
                if(!PyErr_ExceptionMatches(PyExc_KeyError)) throw py_error_set();
                PyErr_Clear();
            }
            return new_ref(item);
#endif
        }

        dict copy(const dict &b) const {
            return new_ref(PyDict_Copy(b._ptr));
        }

        static dict __py_from_pyobject__(PyObject *val) {
            if(!PyDict_Check(val)) THROW_PYERR_STRING(TypeError,"object is not an instance of dict")
            return new_ref(val);
        }
    };

    typedef _nullable<dict> nullable_dict;

    inline void del(const dict_item_proxy &attr) {
        if(PyDict_DelItem(attr._ptr,attr.key) == -1) throw py_error_set();
    }


    template<typename T,int invariable = invariable_storage<T>::value> class pyptr {
        template<typename U> friend class pyptr;

        object _obj;
        T *base;
    public:
        pyptr() : base(0) {}
        pyptr(new_ref r) : _obj(r), base(get_base_or_none<T>(_obj.get())) {}
        pyptr(borrowed_ref r) : _obj(r), base(get_base_or_none<T>(_obj.get())) {}
        pyptr(object o) : _obj(o), base(get_base_or_none<T>(_obj.get())) {}

        template<typename U> pyptr(const pyptr<U> &b) : _obj(b._obj), base(b.base) {}

        template<typename U> pyptr<T> &operator=(const pyptr<U> &b) {
            _obj = b._obj;
            base = b.base;
        }

        T &operator*() { return *base; }
        const T &operator*() const { return *base; }
        T *operator->() { return base; }
        const T *operator->() const { return base; }

        bool operator==(const pyptr &b) const { return _obj == b._obj; }
        bool operator!=(const pyptr &b) const { return _obj != b._obj; }
        operator bool() const { return base != 0; }

        T *get() const { return base; }

        object obj() const { return _obj; }

        void swap(pyptr<T> &b) {
            _obj.swap(b._obj);
            T *tmp = base;
            base = b.base;
            b.base = tmp;
        }
    };

    template<typename T> class pyptr<T,1> {
        template<typename U> friend class pyptr;

        object _obj;

    public:
        pyptr() {}
        pyptr(new_ref r) : _obj(r) { get_base_or_none<T>(_obj.get()); }
        pyptr(borrowed_ref r) : _obj(r) { get_base_or_none<T>(_obj.get()); }
        pyptr(object o) : _obj(o) { get_base_or_none<T>(_obj.get()); }

        template<typename U> pyptr(const pyptr<U> &b) : _obj(b._obj) {
            // a check to make sure an instance of U* is convertable to T*
            T *x = reinterpret_cast<U*>(0);
        }

        template<typename U> pyptr<T> &operator=(const pyptr<U> &b) {
            _obj = b._obj;

            // a check to make sure an instance of U* is convertable to T*
            T *x = reinterpret_cast<U*>(0);
        }

        T &operator*() const { return *get(); }
        T *operator->() const { return get(); }

        bool operator==(const pyptr &b) const { return _obj == b._obj; }
        bool operator!=(const pyptr &b) const { return _obj != b._obj; }
        operator bool() const { return _obj.get() != Py_None; }

        T *get() const { return reinterpret_cast<typename wrapped_type<T>::type*>(_obj.get())->base; }

        object obj() const { return _obj; }

        void swap(pyptr<T> &b) {
            _obj.swap(b._obj);
        }
    };

    // like dynamic_cast except Python's type system is used instead of RTTI
    template<typename T,typename U> inline pyptr<T> python_cast(const pyptr<U> &a) {
        return pyptr<T>(a.obj());
    }


    inline Py_ssize_t len(object o) {
        return PyObject_Length(o.get());
    }

    inline Py_ssize_t len(tuple o) {
        return PyTuple_GET_SIZE(o.get());
    }

    inline Py_ssize_t len(dict o) {
        return PyDict_Size(o.get());
    }



    /*template<typename T> inline pyptr<T> newpy() {
        return new_ref(new wrapped_type<T>::type());
    }

    template<typename T,typename A1> inline pyptr<T> newpy(A1 a1) {
        return new_ref(new wrapped_type<T>::type(a1));
    }

    template<typename T,typename A1,typename A2> inline pyptr<T> newpy(A1 a1,A2 a2) {
        return new_ref(new wrapped_type<T>::type(a1,a2));
    }

    template<typename T,typename A1,typename A2,typename A3> inline pyptr<T> newpy(A1 a1,A2 a2,A3 a3) {
        return new_ref(new wrapped_type<T>::type(a1,a2,a3));
    }

    template<typename T,typename A1,typename A2,typename A3,typename A4> inline pyptr<T> newpy(A1 a1,A2 a2,A3 a3,A4 a4) {
        return new_ref(new wrapped_type<T>::type(a1,a2,a3,a4));
    }*/




    /*template<> inline PyObject *to_pyobject<const std::string&>(const std::string &x) {
        return PyString_FromStringAndSize(x.c_str(),x.size());
    }

    template<> inline PyObject *to_pyobject<const std::wstring&>(const std::wstring &x) {
        return PyUnicode_FromWideChar(x.c_str(),x.size());
    }*/
}


namespace std {
    template<> inline void swap(py::object &a,py::object &b) { a.swap(b); }
    template<> inline void swap(py::tuple &a,py::tuple &b) { a.swap(b); }
    template<> inline void swap(py::dict &a,py::dict &b) { a.swap(b); }
    template<typename T> inline void swap(py::_nullable<T> &a,py::_nullable<T> &b) { a.swap(b); }
    template<typename T> inline void swap(py::pyptr<T> &a,py::pyptr<T> &b) { a.swap(b); }
}


#pragma GCC visibility pop

#endif
