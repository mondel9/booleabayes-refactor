import os
import os.path as op
import resource
import pandas as pd
import numpy as np
import pickle
from graph_tool import all as gt
from graph_tool import GraphView
from sklearn.mixture import GaussianMixture
from scipy.cluster.hierarchy import linkage, dendrogram


# Load the transcription factor network from file
# Return a graph object and vertix dictionary
def load_network(filename, remove_sources=False, remove_sinks=True, remove_selfloops=True, add_selfloops_to_sources=True,
                 header=None):
    if not filename.endswith('.csv') or not os.path.isfile(filename):
        raise Exception('Network path must be a .csv file.  Check file name and location')
        
    G = gt.Graph()
    infile = pd.read_csv(filename, header=header, dtype="str")
    vertex_dict = dict()
    vertex_names = G.new_vertex_property('string')
    vertex_source = G.new_vertex_property('bool')
    vertex_sink = G.new_vertex_property('bool')
    for tf in set(list(infile[0]) + list(infile[1])):
        v = G.add_vertex()
        vertex_dict[tf] = v
        vertex_names[v] = tf

    for i in infile.index:
        if (not remove_selfloops or infile.loc[i, 0] != infile.loc[i, 1]):
            v1 = vertex_dict[infile.loc[i, 0]]
            v2 = vertex_dict[infile.loc[i, 1]]
            if v2 not in v1.out_neighbors(): G.add_edge(v1, v2)

    G.vertex_properties["name"] = vertex_names

    if (remove_sources or remove_sinks):
        G = prune_network(G, remove_sources=remove_sources, remove_sinks=remove_sinks)
        vertex_dict = dict()
        for v in G.vertices():
            vertex_dict[vertex_names[v]] = v

    for v in G.vertices():
        if v.in_degree() == 0:
            if add_selfloops_to_sources: G.add_edge(v, v)
            vertex_source[v] = True
        else:
            vertex_source[v] = False
        if v.out_degree() == 0:
            vertex_sink[v] = True
        else:
            vertex_sink[v] = False

    G.vertex_properties['sink'] = vertex_sink
    G.vertex_properties['source'] = vertex_source

    return G, vertex_dict

def prune_network(G, remove_sources=True, remove_sinks=False):
    oneStripped = True
    while (oneStripped):
        vfilt = G.new_vertex_property('bool')
        oneStripped = False

        for v in G.vertices():
            if (remove_sources and v.in_degree() == 0) or (remove_sinks and v.out_degree() == 0):
                vfilt[v] = False
                oneStripped = True
            else:
                vfilt[v] = True

        G = GraphView(G, vfilt)

    return G

# Reads data from csv; File must have rows=genes, cols=samples
# If norm is one of "gmm" (data are normalized via 2-component gaussian mixture model),
# "minmax" (data are linearly normalized from 0(min) to 1(max)) or no normalization is done
# Return transposed dataframe
def load_data(filename, nodes, log=False, log1p=False, sample_order=None, delimiter=",", norm="gmm", 
              index_col=0, transpose = False, fillna = None):
    if not filename.endswith('.csv') or not os.path.isfile(filename):
        raise Exception('Data path must be a .csv file. Check file name and location.')
        
    data = pd.read_csv(filename, index_col=index_col, delimiter=delimiter, na_values=['null', 'NULL'])
    if transpose: data = data.transpose()
    if index_col > 0: data = data[data.columns[index_col:]]
    data.index = [str(i).upper() for i in data.index]
    missing_nodes = [i for i in nodes if not i in data.index]
    if len(missing_nodes) > 0: raise Warning("Missing nodes: %s" % repr(missing_nodes))
    data = data.loc[nodes]

    if log1p:
        data = np.log(data + 1)
    elif log:
        data = np.log(data)

    df = data.transpose()  # Now: rows=samples, columns=genes
    data = pd.DataFrame(index=df.index, columns=nodes)
    for node in nodes:
        if type(df[node]) == pd.Series:
            data[node] = df[node]
        else:
            data[node] = df[node].mean(axis=1)

    if type(norm) == str:
        if norm.lower() == "gmm":
            gm = GaussianMixture(n_components=2)
            for gene in data.columns:
                d = data[gene].values.reshape(data.shape[0], 1)
                gm.fit(d)

                # Figure out which cluster is ON
                idx = 0
                if gm.means_[0][0] < gm.means_[1][0]: idx = 1

                data[gene] = gm.predict_proba(d)[:, idx]
        elif norm.lower() == "minmax":
            data = (data - data.min()) / (data.max() - data.min())
    elif type(norm) == float:
        if norm > 0 and norm < 1:
            lq = data.quantile(q=norm)
            uq = data.quantile(q=1 - norm)
            data = (data - lq) / (uq - lq)
            data[data < 0] = 0
            data[data > 1] = 1
    if fillna is not None:
        data = data.fillna(fillna)

    if sample_order is None:
        cluster_linkage = linkage(data)
        cluster_dendro = dendrogram(cluster_linkage, no_plot=True)
        cluster_leaves = [data.index[i] for i in cluster_dendro['leaves']]
        data = data.loc[cluster_leaves]
    elif type(sample_order) != bool:  # If sample_order=False, don't sort at all
        data = data.loc[sample_order]

    return data

# filenames is a list of filenames, nodes gives the only genes we are reading, log is True/False, or list of [True, False, True...], delimiter is string, or list of strings
def load_data_multiple(filenames, nodes, log=False, delimiter=",", norm="gmm"):
    datasets = []
    for i, filename in enumerate(filenames):
        if type(log) == list:
            log_i = log[i]
        else:
            log_i = log
        if type(delimiter) == list:
            delimiter_i = delimiter[i]
        else:
            delimiter_i = delimiter

        datasets.append(load_data(filename, nodes, log=log_i, sample_order=False, delimiter=delimiter_i, norm=norm))

    data = pd.concat(datasets)

    cluster_linkage = linkage(data)
    cluster_dendro = dendrogram(cluster_linkage, no_plot=True)
    cluster_leaves = [data.index[i] for i in cluster_dendro['leaves']]
    data = data.loc[cluster_leaves]

    return data

def load_rules(fname="rules.txt", delimiter="|"):
    rules = dict()
    regulators_dict = dict()
    with open(fname,"r") as infile:
        for line in infile:
            line = line.strip().split(delimiter)
            regulators_dict[line[0]] = line[1].split(',')
            rules[line[0]] = np.asarray([float(i) for i in line[2].split(',')])
    return rules, regulators_dict
