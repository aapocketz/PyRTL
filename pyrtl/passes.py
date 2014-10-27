
"""
passes contains structures helpful for writing analysis and
transformation passes over blocks.
"""

import copy
import core
import wire
import helperfuncs

#---------------------------------------------------------------------
#         __   ___          ___  __  ___              ___    __
#    /\  |__) |__   /\     |__  /__`  |  |  |\/|  /\   |  | /  \ |\ |
#   /~~\ |  \ |___ /~~\    |___ .__/  |  |  |  | /~~\  |  | \__/ | \|
#


def area_estimation(tech_in_nm, block=None):
    """ Returns a single number estimating the total area of the block.

    The tech_in_nm is the size of the circuit technology to be estimated,
    with 65 being 65nm and 250 being 0.25um for example.  The area returned
    is in the units of square mm.  The estimates are VERY simple
    """
    raise NotImplementedError


#---------------------------------------------------------------------
#   __   __  ___           __      ___    __
#  /  \ |__)  |  |  |\/| |  /  /\   |  | /  \ |\ |
#  \__/ |     |  |  |  | | /_ /~~\  |  | \__/ | \|
#

def _remove_wire_nets(block):
    """ Remove all wire nodes from the block. """

    def find_producer(x):
        # trace back to the root producer of x
        if x in immediate_producer:
            return find_producer(immediate_producer[x])
        else:
            return x

    immediate_producer = {}  # map from wirevector to its direct producer wirevector
    wire_removal_set = set()  # set of all wirevectors to be removed

    # one pass to build the map of value producers and
    # all of the nets and wires to be removed
    for net in block.logic:
        if net.op == 'w':
            immediate_producer[net.dests[0]] = net.args[0]
            if not isinstance(net.dests[0], wire.Output):
                wire_removal_set.add(net.dests[0])

    # second full pass to create the new logic without the wire nets
    new_logic = set()
    for net in block.logic:
        if net.op != 'w' or isinstance(net.dests[0], wire.Output):
            new_args = tuple(find_producer(x) for x in net.args)
            new_net = core.LogicNet(net.op, net.op_param, new_args, net.dests)
            new_logic.add(new_net)

    # now update the block with the new logic and remove wirevectors
    block.logic = new_logic
    for dead_wirevector in wire_removal_set:
        del block.wirevector_by_name[dead_wirevector.name]
        block.wirevector_set.remove(dead_wirevector)

    block.sanity_check()


def optimize(update_working_block=True, block=None):
    """ Return an optimized version of a synthesized hardware block. """

    block = core.working_block(block)
    for net in block.logic:
        if net.op not in set('r|&~^w'):
            raise core.PyrtlError('error, optimization only works on post-synthesis blocks')
    if not update_working_block:
        block = copy.deepcopy(block)
    _remove_wire_nets(block)
    _constant_propagation(block)
    return block


def _constant_propagation(block):
    """Removes excess constants in the block"""

    def _constant_prop_pass(block):
        """ Does one constant propagation pass """

        def constant_prop_check(net_checking):

            def replace_net(new_net):
                nets_to_remove.add(net_checking)
                nets_to_add.add(new_net)
                for arg_wire in net_checking.args:
                    if arg_wire not in new_net.args:
                        wire_removal_set.add(arg_wire)

            def replace_net_with_const(const_val):
                new_const_wire = wire.Const(bitwidth=1, val=const_val, block=block)
                wire_add_set.add(new_const_wire)
                replace_net_with_wire(const_wire)

            def replace_net_with_wire(new_wire):
                if isinstance(net_checking.dests[0], wire.Output):
                    # if isinstance(new_wire,wire.Input) or isinstance(new_wire,wire.Const):
                    replace_net(core.LogicNet('w', None, args=(new_wire,), 
                                              dests=net_checking.dests))
                else:
                    nets_to_remove.add(net_checking)
                    replacement_wires[net_checking.dests[0]] = new_wire
                    wire_removal_set.add(net_checking.dests)

            one_var_ops = {
                '~': lambda x: ~x,
                'r': lambda x: x   # This is only valid for constant folding purposes
            }
            two_var_ops = {
                '&': lambda l, r: l & r,
                '|': lambda l, r: l | r,
                '^': lambda l, r: l ^ r,
            }
            num_constants = 0
            for arg_wires in net_checking.args:
                if isinstance(arg_wires, wire.Const):
                    num_constants += 1

            if num_constants is 0 or net_checking.op == 'w':
                return None

            if (net_checking.op in two_var_ops) & num_constants is 1:
                # special case
                arg1, arg2 = net_checking.args
                if isinstance(arg1, wire.Const):
                    const_wire = arg1
                    other_wire = arg2
                else:
                    const_wire = arg2
                    other_wire = arg1

                outputs = []
                for other_val in range(0, 1):
                    outputs[other_val] = two_var_ops[net_checking.op](const_wire.val, other_val)

                if outputs[0] == outputs[1]:
                    replace_net_with_const(outputs[0])
                    if isinstance(other_wire, wire.Input):
                        print "Input wire, " + other_wire.name + \
                              ' was eliminated by constant folding'
                elif outputs[0] == 0:
                    replace_net_with_wire(other_wire)
                else:
                    replace_net(core.LogicNet('~', None, args=(other_wire,),
                                              dests=net_checking.dests))

            else:
                if net_checking.op in two_var_ops:
                    output = two_var_ops[net_checking.op](net_checking.args[0],
                                                          net_checking.args[1])
                elif net_checking.op in one_var_ops:
                    output = one_var_ops[net_checking.op](net_checking.args[0])
                else:
                    raise core.PyrtlInternalError('net with invalid op code, '
                                                  + net_checking.op + ' found')

                replace_net_with_const(output)

        def find_producer(x):
            # trace back to the root producer of x
            if x in replacement_wires:
                return replacement_wires[x]
            else:
                return x

        replacement_wires = {}  # map from wire to its producer
        wire_add_set = set()
        wire_removal_set = set()  # set of all wirevectors to be removed
        nets_to_add = set()
        nets_to_remove = set()

        for a_net in block.logic:
            constant_prop_check(a_net)
        # second full pass to cleanup

        new_logic = set()
        for net in block.logic:
            if net not in nets_to_remove:
                new_args = tuple(find_producer(x) for x in net.args)
                new_net = core.LogicNet(net.op, net.op_param, new_args, net.dests)
                new_logic.add(new_net)
        # now update the block with the new logic and remove wirevectors

        new_logic = new_logic.union(nets_to_add)
        block.logic = new_logic
        for new_wirevector in wire_add_set:
            block.add_wirevector(new_wirevector)

        for dead_wirevector in wire_removal_set:
            del block.wirevector_by_name[dead_wirevector.name]
            block.wirevector_set.remove(dead_wirevector)
            ## TODO: add warning about useless input wirevectors

    current_nets = 0
    while len(block.logic) != current_nets:
        current_nets = len(block.logic)
        _constant_prop_pass(block)


#---------------------------------------------------------------------
#    __           ___       ___  __     __
#   /__` \ / |\ |  |  |__| |__  /__` | /__`
#   .__/  |  | \|  |  |  | |___ .__/ | .__/
#

def synthesize(update_working_block=True, block=None):
    """ Lower the design to just single-bit "and", "or", and "not" gates.

    Takes as input a block (default to working block) and creates a new
    block which is identical in function but uses only single bit gates
    and excludes many of the more complicated primitives.  The new block
    should only consist of the combination elements of w, &, |, ^, and ~.
    and sequential elements of registers (which are one bit as well).
    Because memories cannot be broken down to bit-level operations they
    are extracted from the design and made into new input/output interfaces.
    """

    block_in = core.working_block(block)
    block_out = core.Block()
    # resulting block should only have one of a restricted set of net ops
    block_out.legal_ops = set('~&|^rw')
    wirevector_map = {}  # map from (vector,index) -> new_wire
    uid = 0  # used for unique names

    # First step, create all of the new wires for the new block
    # from the original wires and store them in the wirevector_map
    # for reference.
    for wirevector in block_in.wirevector_subset():
        for i in range(len(wirevector)):
            new_name = '_'.join(['synth', wirevector.name, str(i), str(uid)])
            uid += 1
            if isinstance(wirevector, wire.Const):
                new_val = (wirevector.val >> i) & 0x1
                new_wirevector = wire.Const(bitwidth=1, val=new_val, block=block_out)
            else:
                # build the appropriately typed wire (maintaining input/output)
                wirevector_type = type(wirevector)
                new_wirevector = wirevector_type(name=new_name, bitwidth=1, block=block_out)
            wirevector_map[(wirevector, i)] = new_wirevector

    # Now that we have all the wires built and mapped, walk all the blocks
    # and map the logic to the equivalent set of primitives in the system
    for net in block_in.logic:
        _decompose(net, wirevector_map, block_out)

    if update_working_block:
        core.set_working_block(block_out)
    return block_out


def _decompose(net, wv_map, block_out):
    """ Add the wires and logicnets to block_out and wv_map to decompose net """

    def arg(x, i):
        # return the mapped wire vector for argument x, wire number i
        return wv_map[(net.args[x], i)]

    def destlen():
        # return the length of the destination in bits
        return range(len(net.dests[0]))

    def assign_dest(i, v):
        # assign v to the wiremap for dest[0], wire i
        wv_map[(net.dests[0], i)] <<= v

    if net.op == 'w':
        for i in destlen():
            assign_dest(i, arg(0, i))
    elif net.op == '~':
        for i in destlen():
            assign_dest(i, ~arg(0, i))
    elif net.op == '&':
        for i in destlen():
            assign_dest(i, arg(0, i) & arg(1, i))
    elif net.op == '|':
        for i in destlen():
            assign_dest(i, arg(0, i) | arg(1, i))
    elif net.op == '^':
        for i in destlen():
            assign_dest(i, arg(0, i) ^ arg(1, i))
    elif net.op == '=':
        # The == operator is implemented with a nor of xors.
        temp_result = arg(0, 0) ^ arg(1, 0)
        for i in range(1, len(net.args[0])):
            temp_result = temp_result | (arg(0, i) ^ arg(1, i))
        assign_dest(0, ~temp_result)
    elif net.op == 'm':
        for i in destlen():
            muxed_bit = ~arg(0, 0) & arg(1, i) | arg(0, 0) & arg(2, i)
            assign_dest(i, muxed_bit)
    elif net.op == 's':
        for i in destlen():
            selected_bit = arg(0, net.op_param[i])
            assign_dest(i, selected_bit)
    elif net.op == 'c':
        arg_wirelist = []
        # generate list of wires for vectors being concatenated
        for arg_vector in net.args:
            arg_vector_as_list = [wv_map[(arg_vector, i)] for i in range(len(arg_vector))]
            arg_wirelist = arg_vector_as_list + arg_wirelist
        for i in destlen():
            assign_dest(i, arg_wirelist[i])
    elif net.op == 'r':
        for i in destlen():
            args = (arg(0, i),)
            dests = (wv_map[(net.dests[0], i)],)
            new_net = core.LogicNet('r', None, args=args, dests=dests)
            block_out.add_net(new_net)
    elif net.op == '+':
        arg0list = [arg(0, i) for i in range(len(net.args[0]))]
        arg1list = [arg(1, i) for i in range(len(net.args[1]))]
        cin = wire.Const(0, bitwidth=1, block=block_out)
        sumbits, cout = _generate_add(arg0list, arg1list, cin)
        destlist = sumbits + [cout]
        for i in destlen():
            assign_dest(i, destlist[i])
    else:
        raise core.PyrtlInternalError('Unable to synthesize the following net '
                                      'due to unimplemented op :\n%s' % str(net))
    return


def _generate_one_bit_add(a, b, cin):
    """ Generates hardware for a 1-bit full adder. """
    sum = a ^ b ^ cin
    cout = a & b | a & cin | b & cin
    return sum, cout


def _generate_add(a, b, cin):
    """ a and b are lists of wirevectors (all len 1)
        cin is a wirevector (also len 1)
        returns sum as list of wirevectors (all len 1)
        and a carry out wirevector (also len 1)
    """
    if len(a) == 1:
        sumbits, cout = _generate_one_bit_add(a, b, cin)
    else:
        lsbit, ripplecarry = _generate_one_bit_add(a[0], b[0], cin)
        msbits, cout = _generate_add(a[1:], b[1:], ripplecarry)
        sumbits = [lsbit] + msbits  # append to lsb to the lowest bits
    return sumbits, cout