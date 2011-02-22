#ifndef pyobject_h
#define pyobject_h

#include <algorithm>


#ifndef PYEXPOSE_TEMPLATE_HELPERS
    #ifdef pyexpose_common_h
        #error To use pyobject.h, PYEXPOSE_TEMPLATE_HELPERS must be defined before including pyexpose_common.h
    #endif
    #define PYEXPOSE_TEMPLATE_HELPERS
#endif
#include "pyexpose_common.h"


#pragma GCC visibility push(hidden)


#define PY_THROW_STRING(exception,message) { PyErr_SetString(PyExc_##exception,message); throw py_error_set(); }


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
        Py_INCREF(o);
        return o;
    }

    struct borrowed_ref {
        PyObject *_ptr;
        explicit borrowed_ref(PyObject *ptr) : _ptr(ptr) { assert(ptr); }
    };

    struct new_ref {
        PyObject *_ptr;
        explicit new_ref(PyObject *ptr) : _ptr(ptr) { assert(ptr); }
    };

    template<typename T> T *get_base_or_none(PyObject *o) {
        return o == Py_None ? NULL : &get_base<T>(o);
    }


    class object_attr_proxy;
    class object_item_proxy;

    class object {
    protected:
        PyObject *_ptr;

        object(PyObject *ptr) : _ptr(ptr) {}

    public:
        object(borrowed_ref r) : _ptr(incref(r._ptr)) {}
        object(new_ref r) : _ptr(r._ptr) {}
        object(const object &b) : _ptr(incref(b._ptr)) {}

        ~object() {
            Py_DECREF(_ptr);
        }

        object &operator=(const object &b) {
            Py_INCREF(b._ptr);
            Py_DECREF(_ptr);
            _ptr = b._ptr;
            return *this;
        }

        operator bool() {
            return PyObject_IsTrue(_ptr);
        }

        PyObject *get() { return _ptr; }
        const PyObject *get() const { return _ptr; }
        PyObject *get_new_ref() { return incref(_ptr); }

        object_attr_proxy attr(const char *name);

        bool has_attr(const char *name) { return PyObject_HasAttrString(_ptr,name); }
        bool has_attr(object &name) { return PyObject_HasAttr(_ptr,name._ptr); }

        object operator()();
        template<typename A> object operator()(A a);
        template<typename A,typename B> object operator()(A a,B b);
        template<typename A,typename B,typename C> object operator()(A a,B b,C c);

        bool operator==(const object &b) const { return _ptr == b._ptr; }
        bool operator!=(const object &b) const { return _ptr != b._ptr; }

        template<typename T> object_item_proxy at(T key);
        template<typename T> object_item_proxy operator[](T key);

        void swap(object &b) {
            PyObject *tmp = _ptr;
            _ptr = b._ptr;
            b._ptr = tmp;
        }
    };

    template<typename T> inline object make_object(T x) {
        return object(new_ref(to_pyobject(x)));
    }

    class object_attr_proxy {
        friend class object;
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
        template<typename... Args> object operator(Args... args) {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(
                _ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(args).get()...,
                0)));
        }
#else
        object operator()() {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),0)));
        }

        template<typename A> object operator()(A a) {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(a).get(),
                0)));
        }

        template<typename A,typename B> object operator()(A a,B b) {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(a).get(),
                make_object(b).get(),
                0)));
        }

        template<typename A,typename B,typename C> object operator()(A a,B b,C c) {
            return new_ref(check_obj(PyObject_CallMethodObjArgs(_ptr,
                object(new_ref(check_obj(PyString_FromString(name)))).get(),
                make_object(a).get(),
                make_object(b).get(),
                make_object(c).get(),
                0)));
        }
#endif
    };

    inline object_attr_proxy object::attr(const char *name) { return object_attr_proxy(_ptr,name); }

    inline object object::operator()() {
        return new_ref(check_obj(PyObject_CallObject(_ptr,0)));
    }

    template<typename A> inline object object::operator()(A a) {
        return new_ref(check_obj(PyObject_CallFunctionObjArgs(_ptr,
            make_object(a).get(),
            0)));
    }

    template<typename A,typename B> inline object object::operator()(A a,B b) {
        return new_ref(check_obj(PyObject_CallFunctionObjArgs(_ptr,
            make_object(a).get(),
            make_object(b).get(),
            0)));
    }

    template<typename A,typename B,typename C> inline object object::operator()(A a,B b,C c) {
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
        friend class object;
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

        // to make sure object_a[x] = object_b[y] works as expected
        object_item_proxy &operator=(const object_item_proxy &val) {
            return operator=(static_cast<object>(val));
        }
    };

    template<typename T> inline object_item_proxy object::at(T key) {
        return object_item_proxy(_ptr,to_pyobject(key));
    }

    template<typename T> inline object_item_proxy object::operator[](T key) {
        return at<T>(key);
    }

    inline void del(const object_item_proxy &item) {
        if(PyObject_DelItem(item._ptr,item.key) == -1) throw py_error_set();
    }


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

        void *buf() { return view.buf; }
        Py_ssize_t len() { return view.len; }
        int readonly() { return view.readonly; }
        const char *format() { return view.format; }
        int ndim() { return view.ndim; }
        Py_ssize_t *shape() { return view.shape; }
        Py_ssize_t *strides() { return view.strides; }
        Py_ssize_t *suboffsets() { return view.suboffsets; }
        Py_ssize_t itemsize() { return view.itemsize; }
        void *internal() { return view.internal; }
    };
#endif


    class tuple : public object {
    public:
        tuple(borrowed_ref r) : object(r) { assert(PyTuple_Check(r._ptr)); }
        tuple(new_ref r) : object(r) { assert(PyTuple_Check(r._ptr)); }
        explicit tuple(Py_ssize_t len) : object(new_ref(check_obj(PyTuple_New(len)))) {}
        tuple(const tuple &b) : object(b) {}

        tuple &operator=(const tuple &b) {
            object::operator=(b);
            return *this;
        }

        object at(Py_ssize_t i) { return borrowed_ref(check_obj(PyTuple_GetItem(_ptr,i))); }
        void set_unsafe(Py_ssize_t i,PyObject *item) { PyTuple_SET_ITEM(_ptr,i,item); }
        object operator[](Py_ssize_t i) { return borrowed_ref(PyTuple_GET_ITEM(_ptr,i)); }
        Py_ssize_t size() const { return PyTuple_GET_SIZE(_ptr); }
    };


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
    template<typename T> inline void swap(py::pyptr<T> &a,py::pyptr<T> &b) { a.swap(b); }
}


#pragma GCC visibility pop

#endif
