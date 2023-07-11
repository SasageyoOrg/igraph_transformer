
import time
import pickle
import numpy as np

import dgl
import torch

from scipy import sparse as sp
import numpy as np
import networkx as nx

#import hashlib

import torch.nn.functional as F

# ---------------------------------------------------------------------------- #
#                                   Functions                                  #
# ---------------------------------------------------------------------------- #

def positional_encoding(g, pos_enc_dim):
    """
        Graph positional encoding v/ Laplacian eigenvectors
    """

    # Laplacian
    A = g.adjacency_matrix_scipy(return_edge_ids=False).astype(float)
    N = sp.diags(dgl.backend.asnumpy(g.in_degrees()).clip(1) ** -0.5, dtype=float)
    L = sp.eye(g.number_of_nodes()) - N * A * N

    # Eigenvectors with numpy
    EigVal, EigVec = np.linalg.eig(L.toarray())
    idx = EigVal.argsort() # increasing order
    EigVal, EigVec = EigVal[idx], np.real(EigVec[:,idx])
    g.ndata['pos_enc'] = torch.from_numpy(EigVec[:,1:pos_enc_dim+1]).float() 

    # # Eigenvectors with scipy
    # EigVal, EigVec = sp.linalg.eigs(L, k=pos_enc_dim+1, which='SR')
    # EigVec = EigVec[:, EigVal.argsort()] # increasing order
    # g.ndata['pos_enc'] = torch.from_numpy(np.abs(EigVec[:,1:pos_enc_dim+1])).float() 
    
    return g


# ---------------------------------------------------------------------------- #
#                                    Classes                                   #
# ---------------------------------------------------------------------------- #

class IGsDGL(torch.utils.data.Dataset):
    def __init__(self, data_dir, split):
        self.data_dir = data_dir
        self.split = split
        #self.num_graphs = num_graphs
        
        data_path = data_dir + "igraph-GTN-%s.pkl" % self.split
        with open(data_path, "rb") as f:
            self.data = pickle.load(f)

        #self.data = self.data[:100]

        self.graph_labels = []
        self.graph_lists = []
        self.n_samples = len(self.data)
        self._prepare()
        
# ----------------------- Prepare function class IGsDGL ---------------------- #
    def _prepare(self):
        print("preparing %d graphs for the %s set..." % (self.n_samples, self.split.upper()))

        for sample in self.data:
            nx_graph, label = sample
            edge_list = nx.to_edgelist(nx_graph)

            # Create the DGL Graph
            g = dgl.DGLGraph()
            g.add_nodes(nx_graph.number_of_nodes())

            # const 1 features for all nodes and edges; no node features
            g.ndata['feat'] = torch.ones(nx_graph.number_of_nodes(), 1, dtype=torch.float)

            for src, dst, _ in edge_list:
                g.add_edges(src, dst)
                g.add_edges(dst, src)
            g.edata['feat'] = torch.ones(2*len(edge_list), 1, dtype=torch.float)

            y = torch.tensor(label, dtype=torch.long)

            self.graph_lists.append(g)
            self.graph_labels.append(y)
        del self.data

    def __len__(self):
        """Return the number of graphs in the dataset."""
        return self.n_samples

    def __getitem__(self, idx):
        """
            Get the idx^th sample.
            Parameters
            ---------
            idx : int
                The sample index.
            Returns
            -------
            (dgl.DGLGraph, int)
                DGLGraph with node feature stored in `feat` field
                And its label.
        """
        return self.graph_lists[idx], self.graph_labels[idx]

class IGsDatasetDGL(torch.utils.data.Dataset):
    def __init__(self):
        t0 = time.time()
        print("[I] Loading data ...")

        data_dir = "./data/IGs/"
        self.train = IGsDGL(data_dir, 'train')
        self.val = IGsDGL(data_dir, 'val')
        self.test = IGsDGL(data_dir, 'test')

        print("[I] Finished loading.")
        print("Time taken: {:.4f}s".format(time.time()-t0))
    
class IGsDataset(torch.utils.data.Dataset):
    def __init__(self):
        self.name = "IG"
        """
        Loading IGRAPH datasets
        """
        data_dir = 'data/IGs/'

        start = time.time()
        print("[I] Loading dataset IGRAPH...")

        with open(data_dir+'igraph-DatasetDGL.pkl', "rb") as f:
            data = pickle.load(f)
            # datasetDGL = f
            self.train = data.train
            self.val = data.val
            self.test = data.test

        
        # self.train = datasetDGL.train
        # self.val = datasetDGL.val
        # self.test = datasetDGL.test
        
        print('SIZE: train %s, test %s, val %s :' % (len(self.train),len(self.test),len(self.val)))
        print("[I] Finished loading.")
        print("[I] Data load time: {:.4f}s".format(time.time()-start))
        print(f"Data instance example: {self.train[0][0]}")

    # form a mini batch from a given list of samples = [(graph, label) pairs]
    def collate(self, samples):
        # The input samples is a list of pairs (graph, label).
        graphs, labels = map(list, zip(*samples))
        labels = torch.tensor(np.array(labels))
        batched_graph = dgl.batch(graphs)     
    
        return batched_graph, labels 
      
    def _add_positional_encodings(self, pos_enc_dim):
        # Graph positional encoding v/ Laplacian eigenvectors
        self.train.graph_lists = [positional_encoding(g, pos_enc_dim) for g in self.train.graph_lists]
        self.val.graph_lists = [positional_encoding(g, pos_enc_dim) for g in self.val.graph_lists]
        self.test.graph_lists = [positional_encoding(g, pos_enc_dim) for g in self.test.graph_lists]
  
class DGLFormDataset(torch.utils.data.Dataset):
    """
        DGLFormDataset wrapping graph list and label list as per pytorch Dataset.
        *lists (list): lists of 'graphs' and 'labels' with same len().
    """
    def __init__(self, *lists):
        assert all(len(lists[0]) == len(li) for li in lists)
        self.lists = lists
        self.graph_lists = lists[0]
        self.graph_labels = lists[1]

    def __getitem__(self, index):
        return tuple(li[index] for li in self.lists)

    def __len__(self):
        return len(self.lists[0])
