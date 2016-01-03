
"""
The selection graph is a graph representation of the control and data flow
of a function or a basic block.
"""

from collections import namedtuple


SGEdge = namedtuple('SGEdge', ['src', 'dst', 'name', 'kind'])
SGGroup = namedtuple('SGGroup', ['name'])


class SelectionGraph:
    """ Directed acyclic graph of to be selected instructions """
    # TODO: can this derive from graph class?
    def __init__(self):
        self.roots = []
        self.nodes = set()
        self.groups = set()

    def get_node(self, value):
        return value.src()

    def add_node(self, node):
        self.nodes.add(node)

    @property
    def edges(self):
        """ Generate all edges by looking at all inputs for all nodes and then
        finding the nodes that produce these inputs! """
        for node in self.nodes:
            for inp in node.inputs:
                yield SGEdge(inp.node, node, inp.name, inp.kind)

    def check(self):
        """ Check if the graph is consistent """
        for edge in self.edges:
            assert edge.src in self.nodes
            assert edge.dst in self.nodes


class SGValue:
    """ A value is actually an edge in the graph

    it has one single source, and can have multiple usages.
    """
    DATA = 0
    CONTROL = 1
    MEMORY = 2

    def __init__(self, name, kind, node):
        assert kind in (self.DATA, self.CONTROL, self.MEMORY)
        self.name = name
        self.kind = kind  # Control or data value
        self.node = node  # The node producing the value
        self.users = []
        self.vreg = None

    def src(self):
        """ Gets the originating node for this value """
        pass

    def targets(self):
        """ Gets the nodes that use this value """
        pass

    def add_use(self, use):
        self.users.append(use)


class SGNode:
    """ A single node in the selection graph. A node has an operation name
        this can be an abstract operation in case of an unselected graph
        but it can also be a selected instruction in case a machine instruction
        has been selected.

        It also has input values and output values. A node can produce more
        than one value. For instance the 'div' x86 instruction produces both
        the quotient and the remainer.
    """
    def __init__(self, name):
        self.name = name
        self.value = None
        self.inputs = []
        self.outputs = []

    def __repr__(self):
        return '{} [{}]'.format(self.name, self.value)

    def is_machine_instruction(self):
        return False

    def inputs_of_type(self, kind):
        return [inp for inp in self.inputs if inp.kind == kind]

    @property
    def data_inputs(self):
        return self.inputs_of_type(SGValue.DATA)

    @property
    def control_inputs(self):
        return self.inputs_of_type(SGValue.CONTROL)

    @property
    def memory_inputs(self):
        return self.inputs_of_type(SGValue.MEMORY)

    def outputs_of_type(self, kind):
        return [out for out in self.outputs if out.kind == kind]

    @property
    def values(self):
        return self.inputs + self.outputs

    @property
    def data_outputs(self):
        return self.outputs_of_type(SGValue.DATA)

    def add_input(self, x):
        assert isinstance(x, SGValue)
        self.inputs.append(x)
        x.add_use(self)

    def add_inputs(self, *args):
        for inp in args:
            self.add_input(inp)

    def new_output(self, name, kind=SGValue.DATA):
        val = SGValue(name, kind, self)
        self.add_output(val)
        return val

    def usages(self):
        return len(self.outputs)

    def add_output(self, x):
        assert isinstance(x, SGValue)
        assert x.node is self
        self.outputs.append(x)

    @property
    def volatile(self):
        return any(v.kind != SGValue.DATA for v in self.values)