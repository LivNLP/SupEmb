"""
Loads the input matrices created by the createMatrices.py module and
implements the supervised embedding (SupEmb) method.
"""

__author__ = 'Danushka Bollegala'
__license__ = 'BSD'
__date__ = "18/04/2014"


import numpy
import scipy.sparse
from scipy.io import mmwrite, mmread

from sklearn import preprocessing
from sklearn.linear_model import LogisticRegression

import sys
import time

from sparsesvd import sparsesvd

# catch warnings as errors
import warnings
warnings.filterwarnings("always")

import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


def timeit(f):

    def timed(*args, **kw):

        ts = time.time()
        result = f(*args, **kw)
        te = time.time()
        logger.info('func:%r took: %2.4f sec' %  (f.__name__, te-ts))
        return result

    return timed


class SupEmb():

    def __init__(self, Ua, Ub, A, B, XlA_pos, XlA_neg, XuA, XuB):
        self.Ua = Ua 
        logger.info("Ua %d x %d" % Ua.shape)
        self.Ub = Ub
        logger.info("Ub %d x %d" % Ub.shape)
        self.A = A
        logger.info("A %d x %d" % A.shape)
        self.B = B
        logger.info("B %d x %d" % B.shape)
        self.XlA_pos = XlA_pos
        logger.info("XlA_pos %d x %d" % XlA_pos.shape)
        self.XlA_neg = XlA_neg
        logger.info("XlA_neg %d x %d" % XlA_neg.shape)
        self.XuA = XuA
        logger.info("XuA %d x %d" % XuA.shape)
        self.XuB = XuB
        logger.info("XuB %d x %d" % XuB.shape)
        assert(self.Ua.shape[0] == self.Ub.shape[0])
        self.M = self.Ua.shape[0]
        assert(self.Ub.shape[1] == self.B.shape[1])
        assert(self.Ua.shape[1] == self.A.shape[1])
        assert(self.XlA_pos.shape[1] == self.XlA_neg.shape[1])
        assert(self.Ua.shape[0] + self.A.shape[0] == self.XuA.shape[1])
        assert(self.Ub.shape[0] + self.B.shape[0] == self.XuB.shape[1])
        self.d = self.Ua.shape[1]
        self.h = self.Ub.shape[1]

        # parameters of the model.
        self.w1 = 1.0 # weight for Rule 1.
        self.w2 = 1.0 # weight for Rule 2.
        self.w3 = 1.0 # weight for Rule 3.
        self.lambda_1 = 1.0
        self.lambda_2 = 1.0
        self.k2 = 5 # k for k-NN when computing W2
        self.k3 = 5 # k for k-NN when computing W3
        self.k3_bar = 5 # k for k-NN when computing W3_bar
        self.dims = 500 # number of latent dimensions for the embedding
        pass


    @timeit
    def get_W1(self, M):
        """
        Create a 2Mx2M matrix of the form [O I; I O]
        where I is MxM unit matrix and O is MxM zero matrix. 
        """
        O = numpy.zeros((M, M), dtype=float)
        I = numpy.eye(M, dtype=float)
        top = numpy.concatenate((O, I), axis=1)
        bottom = numpy.concatenate((I, O), axis=1)
        W1 = numpy.concatenate((top, bottom), axis=0)
        return W1


    @timeit    
    def get_W2(self):
        # Create the matrix for all labeled instances.
        self.XlA = numpy.concatenate((self.XlA_pos, self.XlA_neg), axis=0)
        self.pos_n = self.XlA_pos.shape[0]
        self.neg_n = self.XlA_neg.shape[0]
        n = self.pos_n + self.neg_n
        self.Y = numpy.concatenate((numpy.ones(self.pos_n), -1 * numpy.ones(self.neg_n)))
        S, neighbours = self.get_kNNs(self.XlA, self.k2)
        W2 = numpy.zeros((n, n), dtype=float)
        for i in range(0, n):
            for j in range(0, n):
                if (j in neighbours[i]) and (i in neighbours[j]):
                    # i and j are undirected nearest neighbours. 
                    if self.Y[i] == self.Y[j]:
                        W2[i,j] = -self.lambda_2 * S[i,j]
                    else:
                        W2[i,j] = S[i,j]
                
        return W2


    @timeit    
    def get_W3(self, X, k):
        """
        Rule 3. Source domain unlabeled documents neighbourhood constraint. 
        """
        S, neighbours = self.get_kNNs(X, k)
        n = X.shape[0]
        W3 = numpy.zeros((n, n), dtype=float)
        for i in range(0, n):
            for j in range(0, n):
                # i and j are undirected nearest neighbours. 
                W3[i,j] = S[i,j]
        return W3


    @timeit
    def get_kNNs(self, A, k):
        """
        A is a matrix where each row represents a document over a set 
        of features represented in columns. We will first normalise 
        the rows in A to unit L2 length and then measure the cosine 
        similarity between all pairs of rows (documents). We will then 
        select the most similar k documents for each document in A.
        We will then return a dictionary of the format
        neighbours[rowid] = numpy.array of neighbouring row ids
        Note that the neighbours within the list are not sorted according
        to their similarity scores to the base document. 

        Args:
            A (numpy.array): document vs. feature matrix. 
            k (integer): nearest neighbours

        Returns:
            S (numpy.array): similarity matrix
            neighbours (dictionary): mapping between row ids and 
                the list of neighbour row ids. 
        """
        # normalise rows in A to unit L2 length.
        normA = preprocessing.normalize(A, norm='l2', copy=True)
        # Compute similarity
        S = numpy.dot(normA, normA.T)
        N = numpy.argsort(S)
        neighbours = {}
        for i in range(S.shape[0]):
            neighbours[i] = N[i,:][-k:]
        return (S, neighbours)


    @timeit  
    def get_Laplacian(self, W):
        """
        Compute the Laplacian of matrix W. 
        """
        # axix=0 column totals, axis=1 row totals.
        s = numpy.array(numpy.sum(W, axis=1))
        D = numpy.diag(s)
        L = W - D
        return L


    def perturbate(self, s):
        """
        Replace zero valued elements in s by the minimum value 
        of s. This must be done to avoid zero division. 
        """
        v = numpy.mean(s)
        zeros = numpy.where(s == 0)
        for i in zeros:
            s[i] = v
        logging.warning("Perturbations = %d" % len(zeros))
        return s


    @timeit
    def get_Dinv(self, W):
        """
        Compute the D^{-1} matrix of W. 
        The (i,i) diagonal element of this diagonal matrix is set
        to 1/sum_of_i_th_row_in_W. 
        """
        s = numpy.array(numpy.sum(W, axis=1))
        if numpy.count_nonzero(s) > 0:
            # zero sum documents exist! 
            s = self.perturbate(s)
        Dinv = numpy.diag(1.0 / s)
        return Dinv


    @timeit
    def get_U1(self):
        """
        U1 = [[Ua O], [0 Ub]]
        """
        Oh = numpy.zeros((self.M, self.h), dtype=float)
        top = numpy.concatenate((self.Ua, Oh), axis=1)
        Od = numpy.zeros((self.M, self.d), dtype=float)
        bottom = numpy.concatenate((Od, self.Ub), axis=1)
        U1 = numpy.concatenate((top, bottom), axis=0)
        return U1

    @timeit
    def get_embedding(self):
        """
        Compute the embedding matrix P. 
        """
        # Compute the components for Rule 1.
        logger.info("Rule1")
        W1 = self.get_W1(self.M)
        L1 = self.get_Laplacian(W1)
        U1 = self.get_U1()
        logger.info("Computing U1.T * L(W1) * U1")
        Qright = numpy.dot(numpy.dot(U1.T, L1), U1)

        # Compute the components for Rule 2.
        logger.info("Rule2")
        logger.info("Computing W2")
        W2 = self.get_W2()
        L2 = self.get_Laplacian(W2)
        D2 = self.get_Dinv(self.XlA)
        totA = numpy.concatenate((self.Ua, self.A), axis=0)
        logger.info("Computing F2")
        part1 = numpy.dot(numpy.dot(D2, self.XlA), totA)
        F2 = numpy.dot(numpy.dot(part1.T, L2), part1)
        
        # Compute the components for Rule 3.
        logger.info("Rule3")
        logger.info("Computing W3")
        W3 = self.get_W3(self.XuA, self.k3)
        L3 = self.get_Laplacian(W3)
        D3 = self.get_Dinv(self.XuA)
        logger.info("Computing F3")
        part2 = numpy.dot(numpy.dot(D3, self.XuA), totA)
        F3 = numpy.dot(numpy.dot(part2.T, L3), part2)

        logger.info("Computing W3_bar")
        W3_bar = self.get_W3(self.XuB, self.k3_bar)
        L3_bar = self.get_Laplacian(W3_bar)
        D3_bar = self.get_Dinv(self.XuB)
        totB = numpy.concatenate((self.Ub, self.B), axis=0)
        logger.info("Computing F3_bar")
        part3 = numpy.dot(numpy.dot(D3_bar, self.XuB), totB)
        F3_bar = numpy.dot(numpy.dot(part3.T, L3_bar), part3)

        logger.info("Computing Q")
        Q11 = (self.w2 * F2) - (self.w3 * F3)
        Q12 = numpy.zeros((self.d, self.h), dtype=float)
        Q22 = -self.w3 * self.lambda_2 * F3_bar
        Qtop = numpy.concatenate((Q11, Q12), axis=1)
        Qbottom = numpy.concatenate((Q12.T, Q22), axis=1)
        Qleft = numpy.concatenate((Qtop, Qbottom), axis=0)
        Q = Qleft - Qright 
        return Q


    @timeit
    def get_projection(self, Q):
        """
        Compute the projection matrices Pa and Pb for the 
        source and the target domains. 
        """
        logger.info("Dimensionality of Q: %d x %d" % Q.shape)
        u, s, v = sparsesvd(scipy.sparse.csc_matrix(Q), self.dims)
        #I = numpy.dot(u, v.T)
        #numpy.testing.assert_array_almost_equal_nulp(I, numpy.eye(self.dims))
        logger.info("Source feature space dimensions = %d" % self.d)
        logger.info("Target feature space dimensions = %d" % self.h)
        Pa = u[:, :self.d]
        Pb = u[:, self.d:]
        logger.info("Pa %d x %d" % Pa.shape)
        logger.info("Pb %d x %d" % Pb.shape)
        return Pa, Pb


    def project_instances(self, X, U, A, P):
        """
        Project train or test instances which are in rows of X 
        according to the projection matrix P.
        """
        Y = numpy.dot(X.T, self.get_Dinv(X))
        left = numpy.dot(U, P.T)
        right = numpy.dot(A, P.T)
        Z = numpy.concatenate((left.T, right.T), axis=1)
        Z = numpy.dot(Z, Y).T
        return Z


    def save_embedding(self, filename, Q):
        """
        Save the embedding Q to a disk file.
        """
        mmwrite(filename, Q)
        pass


    def load_embedding(self, filename):
        """
        Load the embedding from the filename.
        """
        return mmread(filename)


    def check_symmetry(self, Q):
        """
        Checks whether Q is symmetric.
        """
        numpy.testing.assert_array_almost_equal_nulp(Q, Q.T)
        pass


    pass


def load_matrix(fname):
    """
    Loads the matrix from the matrix market format. 
    """
    M = mmread(fname)
    return M.toarray()


@timeit
def train_logistic(pos_train, neg_train):
    """
    Train a binary logistic regression classifier 
    using the positive and negative train instances.
    """
    LR = LogisticRegression(penalty='l2', C=0.1)
    pos_n = pos_train.shape[0]
    neg_n = neg_train.shape[0]
    y =  numpy.concatenate((numpy.ones(pos_n), -1 * numpy.ones(neg_n)))
    X = numpy.concatenate((pos_train, neg_train), axis=0)
    LR.fit(X, y)
    logger.info("Train accuracy = %f " % LR.score(X, y))
    return LR


def test_logistic(pos_test, neg_test, model):
    """
    Classify the test instances using the trained model.
    """
    pos_n = pos_test.shape[0]
    neg_n = neg_test.shape[0]
    y =  numpy.concatenate((numpy.ones(pos_n), -1 * numpy.ones(neg_n)))
    X = numpy.concatenate((pos_test, neg_test), axis=0)
    accuracy = model.score(X, y)
    logger.info("Test accuracy = %f" % accuracy)
    return accuracy


def process(source_domain, target_domain):
    """
    Peform end-to-end processing.
    """
    base_path = "../work/%s-%s" % (source_domain, target_domain)
    Ua = load_matrix("%s/Ua.mtx" % base_path)
    Ub = load_matrix("%s/Ub.mtx" % base_path)
    A = load_matrix("%s/A.mtx" % base_path)
    B = load_matrix("%s/B.mtx" % base_path)
    XlA_pos = load_matrix("%s/XlA_pos.mtx" % base_path)
    XlA_neg = load_matrix("%s/XlA_neg.mtx" % base_path)
    XuA = load_matrix("%s/XuA.mtx" % base_path)
    XuB = load_matrix("%s/XuB.mtx" % base_path)
    SE = SupEmb(Ua, Ub, A, B, XlA_pos, XlA_neg, XuA, XuB)
    Q = SE.get_embedding()
    SE.save_embedding("%s/Q.mtx" % base_path, Q)
    Q = SE.load_embedding("%s/Q.mtx" % base_path)
    #SE.check_symmetry(Q)
    Pa, Pb = SE.get_projection(Q)
    pos_train = SE.project_instances(XlA_pos, Ua, A, Pa)
    neg_train = SE.project_instances(XlA_neg, Ua, A, Pa)
    model = train_logistic(pos_train, neg_train)
    XlB_pos = load_matrix("%s/XlB_pos.mtx" % base_path)
    XlB_neg = load_matrix("%s/XlB_neg.mtx" % base_path)
    pos_test = SE.project_instances(XlB_pos, Ub, B, Pb)
    neg_test = SE.project_instances(XlB_neg, Ub, B, Pb)
    test_logistic(pos_test, neg_test, model)
    pass


def no_adapt_baseline(source_domain, target_domain):
    """
    Implements the no adapt baseline. Train a classifier using source domain
    labeled instances and then evaluate it on target domain test instances.
    """
    base_path = "../work/%s-%s" % (source_domain, target_domain)
    XlA_pos = load_matrix("%s/XlA_pos.mtx" % base_path)
    XlA_neg = load_matrix("%s/XlA_neg.mtx" % base_path)
    XlB_pos = load_matrix("%s/XlB_pos.mtx" % base_path)
    XlB_neg = load_matrix("%s/XlB_neg.mtx" % base_path)
    model = train_logistic(XlA_pos, XlA_neg)
    test_logistic(XlB_pos, XlB_neg, model)
    pass


if __name__ == "__main__":
    source_domain = "books"
    target_domain = "electronics"
    process(source_domain, target_domain)
    #no_adapt_baseline(source_domain, target_domain)
    #process("testSource", "testTarget")

