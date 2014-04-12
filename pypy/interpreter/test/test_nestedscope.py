

class AppTestNestedScope:

    def test_nested_scope(self):
        x = 42
        def f(): return x
        assert f() == 42

    def test_nested_scope2(self):
        x = 42
        y = 3
        def f(): return x
        assert f() == 42

    def test_nested_scope3(self):
        x = 42
        def f():
            def g():
                return x
            return g
        assert f()() == 42

    def test_nested_scope4(self):
        def f():
            x = 3
            def g():
                return x
            a = g()
            x = 4
            b = g()
            return (a, b)
        assert f() == (3, 4)

    def test_nested_scope_locals(self):
        def f():
            x = 3
            def g():
                i = x
                return locals()
            return g()
        d = f()
        assert d == {'i':3, 'x':3}

    def test_deeply_nested_scope_locals(self):
        def f():
            x = 3
            def g():
                def h():
                    i = x
                    return locals()
                return locals(), h()
            return g()
        outer_locals, inner_locals = f()
        assert inner_locals == {'i':3, 'x':3}
        keys = sorted(outer_locals.keys())
        assert keys == ['h', 'x']

    def test_lambda_in_genexpr(self):
        assert [x() for x in (lambda: x for x in range(10))] == list(range(10))

    def test_cell_contents(self):
        def f(x):
            def f(y):
                return x + y
            return f

        g = f(10)
        assert g.__closure__[0].cell_contents == 10

    def test_empty_cell_contents(self):

        def f():
            def f(y):
                return x + y
            return f
            x = 1

        g = f()
        raises(ValueError, "g.__closure__[0].cell_contents")

    def test_compare_cells(self):
        def f(n):
            if n:
                x = n
            def f(y):
                return x + y
            return f

        empty_cell_1 = f(0).__closure__[0]
        empty_cell_2 = f(0).__closure__[0]
        g1 = f(1).__closure__[0]
        g2 = f(2).__closure__[0]
        assert g1 < g2
        assert g1 <= g2
        assert g2 > g1
        assert g2 >= g1
        assert not g1 == g2
        assert g1 != g2
        #
        assert empty_cell_1 == empty_cell_2
        assert not empty_cell_1 != empty_cell_2
        assert empty_cell_1 < g1

    def test_leaking_class_locals(self):
        def f(x):
            class X:
                x = 12
                def f(self):
                    return x
                locals()
            return X
        assert f(1).x == 12

    def test_nested_scope_locals_mutating_cellvars(self):
        def f():
            x = 12
            def m():
                locals()
                x
                locals()
                return x
            return m
        assert f()() == 12


    def test_unbound_local_after_del(self):
        """
        # #4617: It is now legal to delete a cell variable.
        # The following functions must obviously compile,
        # and give the correct error when accessing the deleted name.
        def errorInOuter():
            y = 1
            del y
            print(y)
            def inner():
                return y

        def errorInInner():
            def inner():
                return y
            y = 1
            del y
            inner()

        raises(UnboundLocalError, "errorInOuter()")
        raises(NameError, "errorInInner()")
        """