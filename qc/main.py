import concurrent.futures
import copy
from timeit import default_timer as timer

import picos as pc
import scipy.spatial
from numpy import random
from picos import Problem
from qworder.cascading_rules import Cascader
from qworder.word_generator import WordGenerator
from tqdm import tqdm

from qc.bloch_matrix import *
from qc.data_manager import DataManager, StatesManager, remove_far_points
from qc.gates import Gate


class Program:

    def __init__(self, min_length: int = 3, max_length: int = 4, targets: np.ndarray = np.array([])):
        self.min_length = min_length
        self.max_length = max_length
        self.targets = targets

    def perform_sdp(self, inputs):
        output_a = []
        output_length = []
        for length in range(self.min_length, self.max_length):
            problem = Problem()
            index = length - self.min_length
            quantum_states = inputs[index].states
            number_of_states = len(quantum_states)
            maximally_mixed_state = np.eye(2, dtype=complex) / 2.0001
            # define rescaled quantum state
            rho = pc.HermitianVariable('rho', (2, 2))  # rho scaled with alpha
            problem.add_constraint(rho >> 0)
            visibility = pc.trace(rho)
            # IMPORTANT CONDITION
            problem.add_constraint(visibility <= 1)
            # define probabilities
            probabilities = [pc.RealVariable('p[{0}]'.format(i)) for i in
                             range(number_of_states - 1)]
            problem.add_constraint(1 == pc.sum([probabilities[i] for i in range(number_of_states - 1)]))
            # we want noisy quantum state to belong to convex hull of available states
            problem.add_constraint(
                rho + (1 - visibility) * maximally_mixed_state == pc.sum(
                    [probabilities[i] * quantum_states[i] for i in range(number_of_states - 1)]))
            problem.set_objective("max", visibility)
            # print(problem)
            problem.solve(solver='cvxopt')

            probabilities_values = [float(prob.value) for prob in probabilities]
            maximal_visibility = visibility.value.real
            state_solution = np.array(rho.value_as_matrix) / maximal_visibility
            test_sum = np.zeros((2, 2), dtype=complex)
            for index_state in range(number_of_states):
                test_sum += probabilities_values[index_state] * quantum_states[index_state]
            # test_noisy_state = maximal_visibility * state_solution + (1 - maximal_visibility) * maximally_mixed_state
            #           print('maximal visibility:', maximal_visibility)
            #           print('for state:')
            #           print(test_noisy_state)
            #           print(test_sum)
            #           print(np.linalg.eigvalsh(test_noisy_state))
            #           print(np.trace(test_noisy_state))
            #           print(test_sum - test_noisy_state)
            # raise KeyError
            output_a.append(maximal_visibility)
            output_length.append(length)
        return [output_length, output_a]

    def perform_states(self, input, target):

        outs = []
        previous_out = []

        for length in range(self.min_length, self.max_length):
            index = length - self.min_length
            v = np.concatenate([input[i] for i in range(index + 1)])
            hull = scipy.spatial.ConvexHull(v)
            print(hull.volume)
            v = remove_far_points(np.concatenate([input[i] for i in range(index + 1)]),
                                  target=target, out_length=100)
            n = len(v)

            # LP1
            p = {}
            problem1 = Problem()

            for i in range(n):
                p[i] = pc.RealVariable('p[{0}]'.format(i))
            t1 = pc.RealVariable('t')

            # każde p >= 0
            problem1.add_list_of_constraints([p[i] >= 0 for i in range(n)])
            # p sumują się do 1
            problem1.add_constraint(1 == pc.sum([p[i] for i in range(n)]))
            # wiąz na wektory
            problem1.add_constraint(t1 * target[0] == pc.sum([p[j] * v[j][0] for j in range(n)]))
            problem1.add_constraint(t1 * target[1] == pc.sum([p[j] * v[j][1] for j in range(n)]))
            problem1.add_constraint(t1 * target[2] == pc.sum([p[j] * v[j][2] for j in range(n)]))

            problem1.set_objective("max", t1)
            problem1.solve(solver='cvxopt')

            output1 = sum([float(p[i]) * v[i] for i in range(n)])
            d1 = np.linalg.norm(output1 - target, ord=2)

            # LP2
            r = {}
            q = {}
            problem2 = Problem()

            for i in range(n):
                r[i] = pc.RealVariable('p[{0}]'.format(i))

            for i in range(n):
                q[i] = pc.RealVariable('q[{0}]'.format(i))

            t2 = pc.RealVariable('t')

            # każde p >= 0
            problem2.add_list_of_constraints([r[i] >= 0 for i in range(n)])
            problem2.add_list_of_constraints([q[i] >= 0 for i in range(n)])
            # p sumują się do 1
            problem2.add_constraint(1 == pc.sum([r[i] for i in range(n)]))
            problem2.add_constraint(1 - t2 == pc.sum([q[i] for i in range(n)]))
            # wiąz na wektory
            problem2.add_constraint(t2 * target[0] + pc.sum([q[j] * v[j][0] for j in range(n)]) == pc.sum(
                [r[j] * v[j][0] for j in range(n)]))
            problem2.add_constraint(t2 * target[1] + pc.sum([q[j] * v[j][1] for j in range(n)]) == pc.sum(
                [r[j] * v[j][1] for j in range(n)]))
            problem2.add_constraint(t2 * target[2] + pc.sum([q[j] * v[j][2] for j in range(n)]) == pc.sum(
                [r[j] * v[j][2] for j in range(n)]))

            problem2.set_objective("max", t2)
            problem2.solve(solver='cvxopt')

            output2 = sum([float(r[i]) * v[i] for i in range(n)])
            mix2 = sum([float(q[i]) * v[i] for i in range(n)])
            d2 = np.linalg.norm(output2 - target, ord=2)

            # BRUTE
            output3 = sorted(v, key=lambda vector: np.linalg.norm(vector - target, ord=2))[0]
            d3 = np.linalg.norm(output3 - target, ord=2)

            eps = 10e-5
            out = [length, target.tolist(), float(t1), d1, output1.tolist(), float(t2), d2, output2.tolist(),
                   mix2.tolist(), d3, output3.tolist(), hull.volume]
            print(str([length, float(t2), float(t1)]))
            print(str([length, d2, d1, d3]))
            if not previous_out:
                outs.append(copy.deepcopy(out))
                previous_out = copy.deepcopy(out)
                continue
            if previous_out[3] - d1 >= eps and previous_out[2] - float(t1) <= -eps:
                out1 = out[2:5]
                previous_out[2:5] = out[2:5]
            else:
                temp_out = copy.deepcopy(previous_out)
                out1 = temp_out[2:5]
            if previous_out[6] - d2 >= eps and previous_out[5] - float(t2) <= -eps:
                out2 = out[5:9]
                previous_out[5:9] = out[5:9]
            else:
                temp_out = copy.deepcopy(previous_out)
                out2 = temp_out[5:9]
            res_out = [length, target.tolist()]
            for e in out1:
                res_out.append(e)
            for e in out2:
                res_out.append(e)
            for e in out[9:]:
                res_out.append(e)
            #outs.append([length, target.tolist(), out1[:], out2[:], out[9:]])
            outs.append(res_out)
        return outs

    def perform_channels(self, input, target):

        outs = []
        previous_out = []

        for length in range(self.min_length, self.max_length):
            index = length - self.min_length
            v = remove_far_points(np.concatenate([input[i] for i in range(index + 1)]),
                                  target=target, out_length=200)
            n = len(v)

            # LP1
            p = {}
            problem1 = Problem()

            for i in range(n):
                p[i] = pc.RealVariable('p[{0}]'.format(i))
            t1 = pc.RealVariable('t')

            # każde p >= 0
            problem1.add_list_of_constraints([p[i] >= 0 for i in range(n)])
            # p sumują się do 1
            problem1.add_constraint(1 == pc.sum([p[i] for i in range(n)]))
            # wiąz na wektory
            problem1.add_constraint(t1 * target[0][0] == pc.sum([p[j] * v[j][0][0] for j in range(n)]))
            problem1.add_constraint(t1 * target[0][1] == pc.sum([p[j] * v[j][0][1] for j in range(n)]))
            problem1.add_constraint(t1 * target[0][2] == pc.sum([p[j] * v[j][0][2] for j in range(n)]))
            problem1.add_constraint(t1 * target[1][0] == pc.sum([p[j] * v[j][1][0] for j in range(n)]))
            problem1.add_constraint(t1 * target[1][1] == pc.sum([p[j] * v[j][1][1] for j in range(n)]))
            problem1.add_constraint(t1 * target[1][2] == pc.sum([p[j] * v[j][1][2] for j in range(n)]))
            problem1.add_constraint(t1 * target[2][0] == pc.sum([p[j] * v[j][2][0] for j in range(n)]))
            problem1.add_constraint(t1 * target[2][1] == pc.sum([p[j] * v[j][2][1] for j in range(n)]))
            problem1.add_constraint(t1 * target[2][2] == pc.sum([p[j] * v[j][2][2] for j in range(n)]))

            problem1.set_objective("max", t1)
            problem1.solve(solver='cvxopt')

            output1 = sum([float(p[i]) * v[i] for i in range(n)])
            d1 = np.linalg.norm(output1 - target, ord=2)

            # LP2
            r = {}
            q = {}
            problem2 = Problem()

            for i in range(n):
                r[i] = pc.RealVariable('p[{0}]'.format(i))

            for i in range(n):
                q[i] = pc.RealVariable('q[{0}]'.format(i))

            t2 = pc.RealVariable('t')

            # każde p >= 0
            problem2.add_list_of_constraints([r[i] >= 0 for i in range(n)])
            problem2.add_list_of_constraints([q[i] >= 0 for i in range(n)])
            # p sumują się do 1
            problem2.add_constraint(1 == pc.sum([r[i] for i in range(n)]))
            problem2.add_constraint(1 - t2 == pc.sum([q[i] for i in range(n)]))
            # wiąz na wektory
            problem2.add_constraint(t2 * target[0][0] + pc.sum([q[j] * v[j][0][0] for j in range(n)]) == pc.sum(
                [r[j] * v[j][0][0] for j in range(n)]))
            problem2.add_constraint(t2 * target[0][1] + pc.sum([q[j] * v[j][0][1] for j in range(n)]) == pc.sum(
                [r[j] * v[j][0][1] for j in range(n)]))
            problem2.add_constraint(t2 * target[0][2] + pc.sum([q[j] * v[j][0][2] for j in range(n)]) == pc.sum(
                [r[j] * v[j][0][2] for j in range(n)]))
            problem2.add_constraint(t2 * target[1][0] + pc.sum([q[j] * v[j][1][0] for j in range(n)]) == pc.sum(
                [r[j] * v[j][1][0] for j in range(n)]))
            problem2.add_constraint(t2 * target[1][1] + pc.sum([q[j] * v[j][1][1] for j in range(n)]) == pc.sum(
                [r[j] * v[j][1][1] for j in range(n)]))
            problem2.add_constraint(t2 * target[1][2] + pc.sum([q[j] * v[j][1][2] for j in range(n)]) == pc.sum(
                [r[j] * v[j][1][2] for j in range(n)]))
            problem2.add_constraint(t2 * target[2][0] + pc.sum([q[j] * v[j][2][0] for j in range(n)]) == pc.sum(
                [r[j] * v[j][2][0] for j in range(n)]))
            problem2.add_constraint(t2 * target[2][1] + pc.sum([q[j] * v[j][2][1] for j in range(n)]) == pc.sum(
                [r[j] * v[j][2][1] for j in range(n)]))
            problem2.add_constraint(t2 * target[2][2] + pc.sum([q[j] * v[j][2][2] for j in range(n)]) == pc.sum(
                [r[j] * v[j][2][2] for j in range(n)]))

            problem2.set_objective("max", t2)
            problem2.solve(solver='cvxopt')

            output2 = sum([float(r[i]) * v[i] for i in range(n)])
            mix2 = sum([float(q[i]) * v[i] for i in range(n)])
            d2 = np.linalg.norm(output2 - target, ord=2)

            # BRUTE
            output3 = sorted(v, key=lambda vector: np.linalg.norm(vector - target, ord=2))[0]
            d3 = np.linalg.norm(output3 - target, ord=2)

            if not previous_out:
                outs.append([length, target, float(t1), d1, output1, float(t2), d2, output2, mix2, d3, output3])
                previous_out = [length, target, float(t1), d1, output1, float(t2), d2, output2, mix2, d3, output3]
            elif float(t1) >= previous_out[2] and float(t2) >= previous_out[5]:
                outs.append([length, target, float(t1), d1, output1, float(t2), d2, output2, mix2, d3, output3])
                previous_out = [length, target, float(t1), d1, output1, float(t2), d2, output2, mix2, d3, output3]
            else:
                previous_out[0] += 1
                temp_out = copy.deepcopy(previous_out)
                outs.append(temp_out)

            print(str([length, float(t2), float(t1)]))
            print(str([length, d2, d1, d3]))
        return outs

    def threaded_program(self, gates: list, bloch: BlochMatrix, gate: Gate, program: str, threads: int = 2):

        with concurrent.futures.ProcessPoolExecutor() as executor:
            v = []
            # for each length generate input vectors - independent of target for now
            for length in range(self.min_length, self.max_length):
                wg = WordGenerator(gates, length, cascader=Cascader())
                sm = StatesManager(bloch=bloch, gate=gate, wg=wg)
                if program == "states":
                    v.append(sm.get_vectors())
                elif program == "channels":
                    v.append(sm.get_bloch_matrices())

            results = []
            # Generate target states for each thread
            if program == "states":
                for i in range(threads):
                    results.append(executor.submit(self.perform_states, v, self.targets[i]))
            elif program == "channels":
                for i in range(threads):
                    results.append(executor.submit(self.perform_channels, v, self.targets[i]))

            else:
                return None

            for f in concurrent.futures.as_completed(results):
                try:
                    results.append(f.result())
                except ValueError:
                    print("cannot produce result")
        return results[threads:]

    def generate_target(self, program, amount):
        target = []
        if program == "states":
            rng = np.random.default_rng()
            for _ in range(amount):
                _rn0 = rng.normal(size=3)
                target.append(_rn0 / np.linalg.norm(_rn0))
        elif program == "channels":
            for _ in range(amount):
                target.append(get_random())
        else:
            return False
        return target


if __name__ == "__main__":
    gates = ['H', 'T', 'R', 'X', 'Y', 'Z', 'I']
    writer = DataManager()
    start = timer()
    program_name = "states"
    amount = 1

    program = Program(min_length=1, max_length=12)
    targets = program.generate_target(program_name, amount)

    program.targets = targets
    for vv in tqdm(range(1)):
        vis = round(0.99 - 0.01 * vv, 2)
        res = program.threaded_program(gates=gates, bloch=BlochMatrix(vis=vis), gate=Gate(vis=vis),
                                       program=program_name,
                                       threads=amount)
        writer.write_results(res, vis, program_name)
    end = timer()
    print(f'czas: {end - start} s')
    # writer.file_to_png()
