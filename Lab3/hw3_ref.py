__author__ = 'agrotov'

import itertools
import numpy as np
import lasagne
import theano
import theano.tensor as T
import time
from itertools import count
import query
import os

NUM_EPOCHS = 500

BATCH_SIZE = 1000
NUM_HIDDEN_UNITS = 100
LEARNING_RATE = 0.00005
MOMENTUM = 0.95

POINTWISE = 'pointwise'
PAIRWISE = 'pairwise'
LISTWISE = 'listwise'


# Cut-off level for NDCG metric
ndcg_k = 10

# TODO: Implement the lambda loss function
def lambda_loss(output, lambdas):
    raise "Unimplemented"

# Calculates the best NDCG@k for query with r relevant documents and binary relevance labels
def best_ndcg(r, k):
    if r == 0:
        raise ZeroDivisionError("No relevant documents for given query. NDCG can not be computed.")
    sum_limit = min(r, k)
    b_rank = 1 / np.log(1 + np.array(range(1, sum_limit + 1)))
    return b_rank, np.cumsum(b_rank, axis=0)

# Store the logarithmic discount and normalization factor lists for faster NDCG computation
disc_list, norm_list = best_ndcg(10,10)
disc_list = disc_list#.reshape((-1,1))

# Calculates the NDCG@k for a rank with binary relevance labels assuming a query with r relevant documents
def ndcg(rank, k, r = 1):
    return np.transpose(rank[:k]).dot(disc_list[:k+1]) / norm_list[r-1]


class LambdaRankHW:

    NUM_INSTANCES = count()

    def __init__(self, feature_count, measure_type = POINTWISE):
        self.feature_count = feature_count
        self.output_layer = self.build_model(feature_count,1,BATCH_SIZE)
        self.iter_funcs = self.create_functions(self.output_layer)
        self.measure_type = measure_type

    # train_queries are what load_queries returns - implemented in query.py
    def train_with_queries(self, train_queries, num_epochs, val_queries):
        res = []
        try:
            now = time.time()
            for epoch in self.train(train_queries, val_queries):
                if epoch['number'] % 1 == 0:
                    print("Epoch {} of {} took {:.3f}s".format(
                    epoch['number'], num_epochs, time.time() - now))
                    print("training loss:\t\t{:.6f}".format(epoch['train_loss']))
                    print("val mNDCG:\t\t{:.6f}\n".format(epoch['val_mndcg']))
                    now = time.time()
                    res.append(epoch)
                if epoch['number'] >= num_epochs:
                    break
        except KeyboardInterrupt:
            pass
        return res

    def score(self, query):
        feature_vectors = query.get_feature_vectors()
        scores = self.iter_funcs['out'](feature_vectors)
        return scores


    def build_model(self,input_dim, output_dim,
                    batch_size=BATCH_SIZE):
        """Create a symbolic representation of a neural network with `intput_dim`
        input nodes, `output_dim` output nodes and `num_hidden_units` per hidden
        layer.

        The training function of this model must have a mini-batch size of
        `batch_size`.

        A theano expression which represents such a network is returned.
        """
        print("input_dim",input_dim, "output_dim",output_dim)
        l_in = lasagne.layers.InputLayer(
            shape=(batch_size, input_dim),
        )

        l_hidden = lasagne.layers.DenseLayer(
            l_in,
            num_units=200,
            nonlinearity=lasagne.nonlinearities.tanh,
        )


        l_out = lasagne.layers.DenseLayer(
            l_hidden,
            num_units=output_dim,
            nonlinearity=lasagne.nonlinearities.linear,
        )

        return l_out

    # Create functions to be used by Theano for scoring and training
    def create_functions(self, output_layer,
                          X_tensor_type=T.matrix,
                          batch_size=BATCH_SIZE,
                          learning_rate=LEARNING_RATE, momentum=MOMENTUM, L1_reg=0.0000005, L2_reg=0.000003):
        """Create functions for training, validation and testing to iterate one
           epoch.
        """
        X_batch = X_tensor_type('x')
        y_batch = T.fvector('y')

        output_row = lasagne.layers.get_output(output_layer, X_batch, dtype="float32")
        output = output_row.T

        output_row_det = lasagne.layers.get_output(output_layer, X_batch,deterministic=True, dtype="float32")

        # TODO: Change loss function
        # Point-wise loss function (squared error) - comment it out
        loss_train = lasagne.objectives.squared_error(output,y_batch)
        # Pairwise loss function - comment it in
        # loss_train = lambda_loss(output,y_batch)

        loss_train = loss_train.mean()

        # TODO: (Optionally) You can add regularization if you want - for those interested
        # L1_loss = lasagne.regularization.regularize_network_params(output_layer,lasagne.regularization.l1)
        # L2_loss = lasagne.regularization.regularize_network_params(output_layer,lasagne.regularization.l2)
        # loss_train = loss_train.mean() + L1_loss * L1_reg + L2_loss * L2_reg

        # Parameters you want to update
        all_params = lasagne.layers.get_all_params(output_layer)

        # Update parameters, adam is a particular "flavor" of Gradient Descent
        updates = lasagne.updates.adam(loss_train, all_params)


        # Create two functions:

        # (1) Scoring function, deterministic, does not update parameters, outputs scores
        score_func = theano.function(
            [X_batch],output_row_det,
        )

        # (2) Training function, updates the parameters, outpust loss
        train_func = theano.function(
            [X_batch,y_batch], loss_train,
            updates=updates,
            # givens={
            #     X_batch: dataset['X_train'][batch_slice],
            #     # y_batch: dataset['y_valid'][batch_slice],
            # },
        )

        print("finished create_iter_functions")
        return dict(
            train=train_func,
            out=score_func,
        )

    # TODO: Implement the aggregate (i.e. per document) lambda function
    def lambda_function(self,labels, scores):
        pass


    def compute_lambdas_theano(self,query, labels):
        scores = self.score(query).flatten()
        result = self.lambda_function(labels, scores[:len(labels)])
        return result

    def train_once(self, X_train, query, labels):

        # TODO: Comment out to obtain the lambdas
        # lambdas = self.compute_lambdas_theano(query,labels)
        # lambdas.resize((BATCH_SIZE, ))

        # X_train.resize((BATCH_SIZE, self.feature_count),refcheck=False)
        # Alexandre L. correction
        resize_value = BATCH_SIZE
        if self.measure_type == POINTWISE:
            resize_value = min(resize_value, len(labels))
        X_train.resize((resize_value, self.feature_count), refcheck=False)


        # TODO: Comment out (and comment in) to replace labels by lambdas
        #batch_train_loss = self.iter_funcs['train'](X_train, lambdas)
        batch_train_loss = self.iter_funcs['train'](X_train, labels)
        return batch_train_loss


    def train(self, train_queries, val_queries):
        X_trains = train_queries.get_feature_vectors()

        queries = list(train_queries.values())

        for epoch in itertools.count(1):
            random_batch = np.arange(len(queries))
            np.random.shuffle(random_batch)

            # Calculates training loss
            batch_train_losses = []

            for index in range(len(queries)):
                random_index = random_batch[index]
                labels = queries[random_index].get_labels()

                batch_train_loss = self.train_once(X_trains[random_index],queries[random_index],labels)
                batch_train_losses.append(batch_train_loss)

            avg_train_loss = np.mean(batch_train_losses)

            # Calculates mNDCG on validation set
            val_ndcgs = []
            queries = list(val_queries.values())
            for q in queries:
                labels = np.array(q.get_labels())
                q_scores = -self.score(q).flatten()
                sort_idx = np.argsort(q_scores)
                rank = labels[sort_idx]
                val_ndcgs.append(ndcg(rank, ndcg_k, int(np.sum(labels))))

            val_mndcg = np.mean(val_ndcgs)

            # Return statistics for current epoch
            yield {
                'number': epoch,
                'train_loss': avg_train_loss,
                'val_mndcg': val_mndcg
            }

def experiment(n_epochs, measure_type, num_features, num_folds):

    store_res = {}
    for fold in range(1,num_folds + 1):
        # Load queries from the corresponding fold
        print('\nLoading train queries')
        train_queries = query.load_queries(os.path.normpath('./HP2003/Fold%d/train.txt' % fold), num_features)

        print('\nLoading val queries')
        val_queries = query.load_queries(os.path.normpath('./HP2003/Fold%d/vali.txt' % fold), num_features)

        # Creates a new ranker
        ranker = LambdaRankHW(num_features, measure_type)

        # Stores the statistics for each epoch
        res = ranker.train_with_queries(train_queries, n_epochs, val_queries)

        # Saves the trained ranker
        res.append(ranker)

        # Stores the results for the current fold
        store_res[fold] = res

    return store_res
    #test_queries = query.load_queries(os.path.normpath('./HP2003/Fold%d/test.txt' % fold), num_features)

n_epochs = 5
measure_type = POINTWISE
num_features = 64
num_folds = 1

res = experiment(n_epochs, measure_type, num_features, num_folds)