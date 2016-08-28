import logging
from . import astnodes as ast
from .scope import SemanticError


class TypeChecker:
    """ Type checker """
    logger = logging.getLogger('c3check')

    def __init__(self, diag, context):
        self.diag = diag
        self.context = context

    def check(self):
        """ Check everything """
        for module in self.context.modules:
            self.check_module(module)

    def check_module(self, module: ast.Module):
        """ Check a module """
        assert isinstance(module, ast.Module)
        self.module_ok = True
        self.logger.info('Checking module %s', module.name)
        try:
            # Check defined types of this module:
            for typ in module.types:
                self.check_type(typ)

            # Check global variables:
            for var in module.inner_scope.variables:
                assert not var.isLocal
                self.check_type(var.typ)
        except SemanticError as ex:
            self.error(ex.msg, ex.loc)

        # Check functions:
        for func in module.functions:
            # Try per function, in case of error, continue with next
            try:
                self.check_function(func)
            except SemanticError as ex:
                self.error(ex.msg, ex.loc)

        if not self.module_ok:
            raise SemanticError("Errors occurred", None)

    def check_type(self, typ):
        self.context.check_type(typ)

    def check_function(self, function):
        """ Check a function. """
        for param in function.parameters:
            self.check_type(param.typ)

            # Parameters can only be simple types (pass by value)
            if not self.context.is_simple_type(param.typ):
                raise SemanticError(
                    'Function parameters can only be simple types',
                    function.loc)

        if not self.context.is_simple_type(function.typ.returntype):
            raise SemanticError(
                'Functions can only return simple types', function.loc)

        for sym in function.inner_scope:
            self.check_type(sym.typ)

        if function.body:
            self.check_stmt(function.body)

    def check_stmt(self, code: ast.Statement):
        """ Check a statement """
        try:
            assert isinstance(code, ast.Statement)
            if isinstance(code, ast.Compound):
                for statement in code.statements:
                    self.check_stmt(statement)
            elif isinstance(code, ast.Empty):
                pass
            elif isinstance(code, ast.Assignment):
                self.check_assignment_stmt(code)
            elif isinstance(code, ast.ExpressionStatement):
                # Check that this is always a void function call
                if not isinstance(code.ex, ast.FunctionCall):
                    raise SemanticError('Not a call expression', code.ex.loc)
                value = self.check_function_call(code.ex)
                if not self.context.equal_types('void', code.ex.typ):
                    raise SemanticError(
                        'Can only call void functions', code.ex.loc)
                assert value is None
            elif isinstance(code, ast.If):
                self.check_if_stmt(code)
            elif isinstance(code, ast.Return):
                self.check_return_stmt(code)
            elif isinstance(code, ast.While):
                self.check_while(code)
            elif isinstance(code, ast.For):
                self.check_for_stmt(code)
            elif isinstance(code, ast.Switch):
                self.check_switch_stmt(code)
            else:  # pragma: no cover
                raise NotImplementedError(str(code))
        except SemanticError as exc:
            self.error(exc.msg, exc.loc)

    def check_if_stmt(self, code):
        """ Check if statement """
        self.check_condition(code.condition)
        self.check_stmt(code.truestatement)
        self.check_stmt(code.falsestatement)

    def check_while(self, code):
        """ Check while statement """
        self.check_condition(code.condition)
        self.check_stmt(code.statement)

    def check_for_stmt(self, code):
        """ Check a for-loop """
        self.check_stmt(code.init)
        self.check_condition(code.condition)
        self.check_stmt(code.statement)
        self.check_stmt(code.final)

    def check_switch_stmt(self, switch):
        """ Check a switch statement """
        self.check_expr(switch.expression, rvalue=True)
        if not self.context.equal_types('int', switch.expression.typ):
            raise SemanticError(
                'Switch condition must be integer', switch.expression.loc)

        default_block = False
        for option_val, option_code in switch.options:
            self.check_stmt(option_code)

            if option_val is None:
                # default case
                default_block = True
            else:
                self.check_expr(option_val)

        if not default_block:
            raise SemanticError(
                'No default case specified in switch-case', switch.loc)

    def check_return_stmt(self, code):
        """ Check a return statement """
        if code.expr:
            self.check_expr(code.expr, rvalue=True)

    def check_assignment_stmt(self, code):
        """ Check code for assignment statement """
        # Evaluate left hand side:
        self.check_expr(code.lval)

        # Check that the left hand side is a simple type:
        if not self.context.is_simple_type(code.lval.typ):
            raise SemanticError(
                'Cannot assign to complex type {}'.format(code.lval.typ),
                code.loc)

        # Check that left hand is an lvalue:
        if not code.lval.lvalue:
            raise SemanticError(
                'No valid lvalue {}'.format(code.lval), code.lval.loc)

        # Evaluate right hand side (and make it rightly typed):
        self.check_expr(code.rval, rvalue=True)
        code.rval = self.do_coerce(code.rval, code.lval.typ)

    def check_condition(self, expr):
        """ Check condition expression """
        if isinstance(expr, ast.Binop):
            if expr.op in ['and', 'or']:
                self.check_condition(expr.a)
                self.check_condition(expr.b)
            elif expr.op in ['==', '>', '<', '!=', '<=', '>=']:
                self.check_expr(expr.a, rvalue=True)
                self.check_expr(expr.b, rvalue=True)
                expr.b = self.do_coerce(expr.b, expr.a.typ)
            else:
                raise SemanticError('non-bool: {}'.format(expr.op), expr.loc)
            expr.typ = self.context.get_type('bool')
        elif isinstance(expr, ast.Literal):
            self.check_expr(expr)
        elif isinstance(expr, ast.Unop) and expr.op == 'not':
            self.check_condition(expr.a)
            expr.typ = self.context.get_type('bool')
        elif isinstance(expr, ast.Expression):
            # Evaluate expression, make sure it is boolean and compare it
            # with true:
            self.check_expr(expr, rvalue=True)
        else:  # pragma: no cover
            raise NotImplementedError(str(expr))

        # Check that the condition is a boolean value:
        if not self.context.equal_types(expr.typ, 'bool'):
            self.error('Condition must be boolean', expr.loc)

    def check_expr(self, expr: ast.Expression, rvalue=False):
        """ Check an expression. """
        assert isinstance(expr, ast.Expression)
        if self.is_bool(expr):
            self.check_bool_expr(expr)
        else:
            if isinstance(expr, ast.Binop):
                self.check_binop(expr)
            elif isinstance(expr, ast.Unop):
                self.check_unop(expr)
            elif isinstance(expr, ast.Identifier):
                self.check_identifier(expr)
            elif isinstance(expr, ast.Deref):
                self.check_dereference(expr)
            elif isinstance(expr, ast.Member):
                self.check_member_expr(expr)
            elif isinstance(expr, ast.Index):
                self.check_index_expr(expr)
            elif isinstance(expr, ast.Literal):
                self.check_literal_expr(expr)
            elif isinstance(expr, ast.TypeCast):
                self.check_type_cast(expr)
            elif isinstance(expr, ast.Sizeof):
                self.check_sizeof(expr)
            elif isinstance(expr, ast.FunctionCall):
                self.check_function_call(expr)
            else:  # pragma: no cover
                raise NotImplementedError(str(expr))

        # do rvalue trick here, create a r-value when required:
        if rvalue and expr.lvalue:
            # Generate expression code and insert an extra load instruction
            # when required.
            # This means that the value can be used in an expression or as
            # a parameter.

            val_typ = self.context.get_type(expr.typ)
            if not isinstance(val_typ, (ast.PointerType, ast.BaseType)):
                raise SemanticError(
                    'Cannot deref {}'.format(val_typ), expr.loc)

            # This expression is no longer an lvalue
            expr.lvalue = False

    def check_bool_expr(self, expr):
        """ Check boolean expression """
        self.check_condition(expr)

        # This is for sure no lvalue:
        expr.lvalue = False

    def check_sizeof(self, expr):
        # This is not a location value..
        expr.lvalue = False

        # The type of this expression is int:
        expr.typ = self.context.get_type('int')

        self.check_type(expr.query_typ)

    def check_dereference(self, expr: ast.Deref):
        """ dereference pointer type, which means *(expr) """
        assert isinstance(expr, ast.Deref)

        # Make sure to have the rvalue of the pointer:
        self.check_expr(expr.ptr, rvalue=True)

        # A pointer is always a lvalue:
        expr.lvalue = True

        ptr_typ = self.context.get_type(expr.ptr.typ)
        if not isinstance(ptr_typ, ast.PointerType):
            raise SemanticError('Cannot deref {}'.format(ptr_typ), expr.loc)
        expr.typ = ptr_typ.ptype

    def check_unop(self, expr):
        """ Check unary operator """
        if expr.op == '&':
            self.check_expr(expr.a)
            if not expr.a.lvalue:
                raise SemanticError('No valid lvalue', expr.a.loc)
            expr.typ = ast.PointerType(expr.a.typ)
            expr.lvalue = False
        elif expr.op in ['+', '-']:
            self.check_expr(expr.a, rvalue=True)
            expr.typ = expr.a.typ
            expr.lvalue = False
        else:  # pragma: no cover
            raise NotImplementedError(str(expr.op))

    def check_binop(self, expr: ast.Binop):
        """ Check binary operation """
        assert isinstance(expr, ast.Binop)
        assert expr.op not in ast.Binop.cond_ops
        expr.lvalue = False

        # Dealing with simple arithmatic
        self.check_expr(expr.a, rvalue=True)
        self.check_expr(expr.b, rvalue=True)

        # Get best type for result:
        common_type = self.context.get_common_type(expr.a, expr.b)
        expr.typ = common_type

        # TODO: check if operation can be performed on shift and bitwise
        if expr.op not in ['+', '-', '*', '/', '%', '<<', '>>', '|', '&', '^']:
            raise SemanticError("Cannot use {}".format(expr.op))

        # Perform type coercion:
        expr.a = self.do_coerce(expr.a, common_type)
        expr.b = self.do_coerce(expr.b, common_type)

    def check_identifier(self, expr):
        """ Check identifier usage """
        # Generate code for this identifier.
        target = self.context.resolve_symbol(expr)

        # This returns the dereferenced variable.
        if isinstance(target, ast.Variable):
            expr.lvalue = True
            expr.typ = target.typ
        elif isinstance(target, ast.Constant):
            expr.lvalue = False
            expr.typ = target.typ
        else:
            raise SemanticError(
                'Cannot use {} in expression'.format(target), expr.loc)

    def check_member_expr(self, expr):
        """ Check expressions such as struc.mem """
        if self.is_module_ref(expr):
            # Damn, we are referring something inside another module!
            # Invoke scope machinery!
            target = self.context.resolve_symbol(expr)
            if isinstance(target, ast.Variable):
                expr.lvalue = True
                expr.typ = target.typ
            else:  # pragma: no cover
                raise NotImplementedError(str(target))
            return

        self.check_expr(expr.base)

        # The base is a valid expression:
        expr.lvalue = expr.base.lvalue
        basetype = self.context.get_type(expr.base.typ)
        if isinstance(basetype, ast.StructureType):
            self.check_type(basetype)
            if basetype.has_field(expr.field):
                expr.typ = basetype.field_type(expr.field)
            else:
                raise SemanticError('{} does not contain field {}'
                                    .format(basetype, expr.field),
                                    expr.loc)
        else:
            raise SemanticError('Cannot select {} of non-structure type {}'
                                .format(expr.field, basetype), expr.loc)

        # expr must be lvalue because we handle with addresses of variables
        assert expr.lvalue

    def is_module_ref(self, expr):
        """ Determine whether a module is referenced """
        if isinstance(expr, ast.Member):
            if isinstance(expr.base, ast.Identifier):
                target = self.context.resolve_symbol(expr.base)
                return isinstance(target, ast.Module)
            elif isinstance(expr, ast.Member):
                return self.is_module_ref(expr.base)
        return False

    def check_index_expr(self, expr):
        """ Array indexing """
        self.check_expr(expr.base)
        self.check_expr(expr.i, rvalue=True)

        base_typ = self.context.get_type(expr.base.typ)
        if not isinstance(base_typ, ast.ArrayType):
            raise SemanticError('Cannot index non-array type {}'
                                .format(base_typ),
                                expr.base.loc)

        # Make sure the index is an integer:
        expr.i = self.do_coerce(expr.i, 'int')

        # Base address must be a location value:
        assert expr.base.lvalue
        expr.typ = base_typ.element_type
        expr.lvalue = True

    def check_literal_expr(self, expr):
        """ Check literal """
        expr.lvalue = False
        typemap = {int: 'int',
                   float: 'double',
                   bool: 'bool',
                   str: 'string'}
        if isinstance(expr.val, tuple(typemap.keys())):
            expr.typ = self.context.get_type(typemap[type(expr.val)])
        else:
            raise SemanticError('Unknown literal type {}'
                                .format(expr.val), expr.loc)

    def check_type_cast(self, expr):
        """ Check type cast """
        # When type casting, the rvalue property is lost.
        self.check_expr(expr.a, rvalue=True)
        expr.lvalue = False

        expr.typ = expr.to_type

    def check_function_call(self, expr):
        """ Check function call """
        # Lookup the function in question:
        target_func = self.context.resolve_symbol(expr.proc)
        if not isinstance(target_func, ast.Function):
            raise SemanticError('cannot call {}'.format(target_func), expr.loc)
        ftyp = target_func.typ
        fname = target_func.package.name + '_' + target_func.name

        # Check arguments:
        ptypes = ftyp.parametertypes
        if len(expr.args) != len(ptypes):
            raise SemanticError('{} requires {} arguments, {} given'
                                .format(fname, len(ptypes), len(expr.args)),
                                expr.loc)

        # Evaluate the arguments:
        new_args = []
        for arg_expr, arg_typ in zip(expr.args, ptypes):
            self.check_expr(arg_expr, rvalue=True)
            arg_expr = self.do_coerce(arg_expr, arg_typ)
            new_args.append(arg_expr)
        expr.args = new_args

        # determine return type:
        expr.typ = ftyp.returntype

        # Return type will never be an lvalue:
        expr.lvalue = False

        if not self.context.is_simple_type(ftyp.returntype):
            raise SemanticError(
                'Return value can only be a simple type', expr.loc)

    def do_coerce(self, expr, typ):
        """ Try to convert expression into the given type.

        typ: the type of the value
        wanted_typ: the type that it must be
        loc: the location where this is needed.
        Raises an error is the conversion cannot be done.
        """
        if self.context.equal_types(expr.typ, typ):
            # no cast required
            pass
        elif isinstance(expr.typ, ast.PointerType) and \
                isinstance(typ, ast.PointerType):
            # Pointers are pointers, no matter the pointed data.
            expr = ast.TypeCast(typ, expr, expr.loc)
        elif self.context.equal_types('int', expr.typ) and \
                isinstance(typ, ast.PointerType):
            expr = ast.TypeCast(typ, expr, expr.loc)
        elif self.context.equal_types('int', expr.typ) and \
                self.context.equal_types('byte', typ):
            expr = ast.TypeCast(typ, expr, expr.loc)
        elif self.context.equal_types('int', expr.typ) and \
                self.context.equal_types('double', typ):
            expr = ast.TypeCast(typ, expr, expr.loc)
        elif self.context.equal_types('double', expr.typ) and \
                self.context.equal_types('float', typ):
            expr = ast.TypeCast(typ, expr, expr.loc)
        elif self.context.equal_types('float', expr.typ) and \
                self.context.equal_types('double', typ):
            expr = ast.TypeCast(typ, expr, expr.loc)
        elif self.context.equal_types('byte', expr.typ) and \
                self.context.equal_types('int', typ):
            expr = ast.TypeCast(typ, expr, expr.loc)
        else:
            raise SemanticError(
                "Cannot use '{}' as '{}'".format(expr.typ, typ), expr.loc)
        self.check_expr(expr)
        return expr

    def error(self, msg, loc=None):
        """ Emit error to diagnostic system and mark package as invalid """
        self.module_ok = False
        self.diag.error(msg, loc)

    def is_bool(self, expr):
        """ Check if an expression is a boolean type """
        if isinstance(expr, ast.Binop) and expr.op in ast.Binop.cond_ops:
            return True
        elif isinstance(expr, ast.Unop) and expr.op in ast.Unop.cond_ops:
            return True
        else:
            return False
