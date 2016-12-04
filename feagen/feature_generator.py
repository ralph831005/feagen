import os.path
import inspect
import re
from collections import defaultdict

import h5py
from mkdir_p import mkdir_p
import six
import networkx as nx

from .data_handler import (
    MemoryIntermediateDataHandler,
    HDF5DataHandler,
)


def draw_dag(nx_dag, path):
    agraph = nx.nx_agraph.to_agraph(nx_dag)
    for edge in agraph.edges_iter():
        edge.attr['label'] = edge.attr['keys']
    agraph.layout('dot')
    agraph.draw(path)


class DataDAG(object):
    # TODO: This is not really a DAG

    def __init__(self):
        self._data_key_node_dict = {}
        self._nx_dag = nx.DiGraph()

    def add_node(self, function_name, function):
        # pylint: disable=protected-access
        config = function._feagen_will_generate

        for key in config['keys']:
            if key in self._data_key_node_dict:
                raise ValueError("duplicated data key '{}' in {} and {}".format(
                    key, self._data_key_node_dict[key], function_name))
            self._data_key_node_dict[key] = function_name

        self._nx_dag.add_node(function_name, config, function=function)

    def get_node_keys_dict(self, data_keys):
        node_keys_dict = defaultdict(list)
        for data_key in data_keys:
            node_keys_dict[self[data_key]].append(data_key)
        return node_keys_dict

    def add_edges_from(self, requirements):
        edges = []
        for function_name, function_requirements in requirements:
            node_keys_dict = self.get_node_keys_dict(function_requirements)
            for source_node, data_keys in six.viewitems(node_keys_dict):
                edges.append((source_node, function_name, {'keys': data_keys}))
        self._nx_dag.add_edges_from(edges)
        if not nx.is_directed_acyclic_graph(self._nx_dag):
            raise ValueError("The dependency graph has cycle.")

    def __getitem__(self, key):
        found_node = None
        found_key = None
        for regex_key, node in six.viewitems(self._data_key_node_dict):
            if re.match("(%s)$" % regex_key, key) is not None:
                if found_node is None:
                    found_node = node
                    found_key = regex_key
                else:
                    raise ValueError("The data key '{}' matches multiple keys: "
                                     "'{}' in {} and '{}' in {}.".format(
                                         key, found_key, found_node,
                                         regex_key, node))
        if found_node is None:
            raise KeyError(key)
        return found_node

    def draw(self, path):
        draw_dag(self._nx_dag, path)

    def get_subgraph_with_ancestors(self, nodes):
        subgraph_nodes = set(nodes)
        for node in nodes:
            subgraph_nodes |= nx.ancestors(self._nx_dag, node)
        return self._nx_dag.subgraph(subgraph_nodes)


class FeatureGeneratorType(type):

    def __init__(cls, name, bases, attrs):
        # pylint: disable=protected-access
        super(FeatureGeneratorType, cls).__init__(name, bases, attrs)

        attrs = inspect.getmembers(
            cls, lambda a: hasattr(a, '_feagen_will_generate'))

        dag = DataDAG()
        # build DAG
        requirements = []
        handler_set = set()
        for function_name, function in attrs:
            dag.add_node(function_name, function)
            handler_set.add(function._feagen_will_generate['handler'])
            del function._feagen_will_generate
            if hasattr(function, '_feagen_require'):
                requirements.append((function_name, function._feagen_require))
                del function._feagen_require
        dag.add_edges_from(requirements)
        cls._dag = dag
        cls._handler_set = handler_set


class DataGenerator(six.with_metaclass(FeatureGeneratorType, object)):

    def __init__(self, handlers):
        handler_set = set(six.viewkeys(handlers))
        if handler_set != self._handler_set:
            redundant_handlers_set = handler_set - self._handler_set
            lacked_handlers_set = self._handler_set - handler_set
            raise ValueError('Handler set mismatch. {} redundant and {} lacked.'
                             .format(redundant_handlers_set,
                                     lacked_handlers_set))
        self._handlers = handlers

    def generate(self, data_keys):
        if isinstance(data_keys, str):
            data_keys = (data_keys,)

        # get the nodes ad edges that should be considered during the generation
        node_keys_dict = self._dag.get_node_keys_dict(data_keys)
        involved_dag = self._dag.get_subgraph_with_ancestors(
            six.viewkeys(node_keys_dict))
        generation_order = nx.topological_sort(involved_dag)
        edges = []
        for source_node, data_keys in six.viewitems(node_keys_dict):
            edges.append((source_node, 'generate', {'keys': data_keys}))
        involved_dag.add_edges_from(edges)
        import ipdb; ipdb.set_trace()

        return involved_dag

    @classmethod
    def draw_dag(cls, path):
        cls._dag.draw(path)  # pylint: disable=protected-access


class FeatureGenerator(DataGenerator):

    def __init__(self, global_feature_hdf_path=None, handlers=None):
        if handlers is None:
            handlers = {}
        if ('intermediate_data' in self._handler_set
                and 'intermediate_data' not in handlers):
            handlers['intermediate_data'] = MemoryIntermediateDataHandler()
        if ('features' in self._handler_set
                and 'features' not in handlers):
            if global_feature_hdf_path is None:
                raise ValueError("global_feature_hdf_path should be specified "
                                 "when initiating FeatureGenerator.")
            handlers['features'] = HDF5DataHandler(global_feature_hdf_path)
        super(FeatureGenerator, self).__init__(handlers)

