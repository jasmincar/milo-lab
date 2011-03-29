#!/usr/bin/python

import pylab
import numpy as np
import logging
import csv
from toolbox.linear_regression import LinearRegression
from toolbox.database import SqliteDatabase
from toolbox.html_writer import HtmlWriter
from pygibbs.nist import Nist
from pygibbs.kegg import Kegg
from pygibbs.group_decomposition import GroupDecomposer
from pygibbs.thermodynamics import Thermodynamics,\
    MissingCompoundFormationEnergy, PsuedoisomerTableThermodynamics
from pygibbs.pseudoisomers_data import DissociationTable
from pygibbs.pseudoisomer import PseudoisomerMap
from pygibbs.thermodynamic_constants import default_I, default_pMg, default_T,\
    default_pH
import os
from toolbox.util import _mkdir
from pygibbs import kegg_reaction
from toolbox.sparse_kernel import SparseKernel

class NistAnchors(object):
    
    def __init__(self, db, html_writer):
        self.db = db
        self.html_writer = html_writer
        self.cid2dG0_f = {}
        self.cid2min_nH = {}
        
    def __len__(self):
        return len(self.cid2dG0_f)
        
    def FromCsvFile(self, filename='../data/thermodynamics/nist_anchors.csv'):
        self.db.CreateTable('nist_anchors', 'cid INT, z INT, nH INT, nMg INT, dG0 REAL')
        for row in csv.DictReader(open(filename, 'r')):
            if bool(row['skip']) and row['skip'] != 'False':
                continue
            cid = int(row['cid'])
            dG0_f = float(row['dG0'])
            z = int(row['z'])
            nH = int(row['nH'])
            nMg = int(row['nMg'])
            self.db.Insert('nist_anchors', [cid, dG0_f, nH, z, nMg])
        
            self.cid2dG0_f[cid] = dG0_f
            self.cid2min_nH[cid] = nH
        
    def FromDatabase(self):
        for row in self.db.DictReader('nist_anchors'):
            self.cid2dG0_f[row['cid']] = row['dG0']
            self.cid2min_nH[row['cid']] = row['nH']
            
    def Load(self):
        if not self.db.DoesTableExist('nist_anchors'):
            self.FromCsvFile()
        else:
            self.FromDatabase()
            
    def GetAllCids(self):
        return sorted(self.cid2dG0_f.keys())

class NistRegression(Thermodynamics):
    
    def __init__(self, db, html_writer, nist=None):
        Thermodynamics.__init__(self)
        self.db = db
        self.html_writer = html_writer
        self.kegg = Kegg.getInstance()
        self.nist = nist or Nist()
        
        self.nist_anchors = NistAnchors(self.db, self.html_writer)
        self.nist_anchors.FromCsvFile()
        
        self.cid2diss_table = DissociationTable.ReadDissociationCsv()
        self.cid2pmap_dict = {}
        
        self.assume_no_pKa_by_default = False
        self.std_diff_threshold = np.inf
        
    def cid2PseudoisomerMap(self, cid):
        if (cid in self.cid2pmap_dict):
            return self.cid2pmap_dict[cid]
        else:
            raise MissingCompoundFormationEnergy("The compound C%05d does not have a value for its formation energy of any of its pseudoisomers" % cid, cid)

    def get_all_cids(self):
        return sorted(self.cid2pmap_dict.keys())
        
    def ReverseTranformNistRows(self, nist_rows):
        all_cids_with_pKa = set(self.cid2diss_table.keys())
        all_cids_in_nist = set(self.nist.GetAllCids())
        
        if self.assume_no_pKa_by_default:
            for cid in all_cids_in_nist.difference(all_cids_with_pKa):
                diss = DissociationTable(cid)
                diss.min_nH = self.kegg.cid2num_hydrogens(cid)
                diss.min_charge = self.kegg.cid2charge(cid)
                if diss.min_nH == None or diss.min_charge == None:
                    logging.warning('cannot add C%05d since nH or charge '
                                    'cannot be determined' % cid) 
                else:
                    self.cid2diss_table[cid] = diss
                    all_cids_with_pKa.add(cid)

        data = {}
        data['cids_to_estimate'] = sorted(all_cids_with_pKa)
        
        # the transformed (observed) free energy of the reactions dG'0_r
        data['dG0_r_tag'] = np.zeros((0, 1))
        
        # dG'0_r - dG0_r  (which is only a function of the conditions and pKas)
        data['ddG0_r'] = np.zeros((0, 1))
        
        data['pH'] = np.zeros((0, 1))
        data['I'] = np.zeros((0, 1))
        data['pMg'] = np.zeros((0, 1))
        data['T'] = np.zeros((0, 1))
        data['S'] = np.zeros((0, len(data['cids_to_estimate']))) # stoichiometric matrix
        
        for nist_row_data in nist_rows:
            # check that all participating compounds have a known pKa
            cids_in_reaction = set(nist_row_data.sparse.keys())
            cids_without_pKa = cids_in_reaction.difference(all_cids_with_pKa)
            if cids_without_pKa:
                logging.debug('reaction contains CIDs with unknown pKa values: %s' % \
                              ', '.join(['C%05d' % cid for cid in cids_without_pKa]))
                continue
            
            data['dG0_r_tag'] = np.vstack([data['dG0_r_tag'], nist_row_data.dG0_r])
            data['pH'] = np.vstack([data['pH'], nist_row_data.pH])
            data['I'] = np.vstack([data['I'], nist_row_data.I])
            data['pMg'] = np.vstack([data['pMg'], nist_row_data.pMg])
            data['T'] = np.vstack([data['T'], nist_row_data.T])
            ddG = self.ReverseTransformReaction(nist_row_data.sparse, 
                nist_row_data.pH, nist_row_data.I, nist_row_data.pMg,
                nist_row_data.T)
            data['ddG0_r'] = np.vstack([data['ddG0_r'], ddG])
            
            stoichiometric_row = np.zeros((1, len(data['cids_to_estimate'])))
            for cid, coeff in nist_row_data.sparse.iteritems():
                stoichiometric_row[0, data['cids_to_estimate'].index(cid)] = coeff
            
            data['S'] = np.vstack([data['S'], stoichiometric_row])
        
        data['dG0_r'] = data['dG0_r_tag'] - data['ddG0_r']
        
        # remove the columns that are all-zeros in S
        nonzero_columns = pylab.find(pylab.sum(abs(data['S']), 0))
        data['S'] = data['S'][:, nonzero_columns]
        data['cids_to_estimate'] = pylab.array(data['cids_to_estimate'])
        data['cids_to_estimate'] = data['cids_to_estimate'][nonzero_columns]
        
        return data

    def ReverseTransform(self, use_anchors=False):
        """
            Performs the reverse Lagandre transform on all the data in NIST where
            it is possible, i.e. where all reactants have pKa values in the range
            (pH-2, pH+2) - the pH in which the Keq was measured.
        """
        logging.info("Reverse transforming the NIST data")
        
        nist_rows = self.nist.SelectRowsFromNist()
        data = self.ReverseTranformNistRows(nist_rows)
        
        stoichiometric_matrix = data['S']
        cids_to_estimate = data['cids_to_estimate']
        
        logging.info("%d out of %d compounds are anchored" % \
                     (len(self.nist_anchors), len(cids_to_estimate)))
        logging.info("%d out of %d NIST measurements can be used" % \
                     (stoichiometric_matrix.shape[0], len(self.nist.data)))

        # squeeze the regression matrix by leaving only unique rows
        unique_rows_S = pylab.unique([tuple(stoichiometric_matrix[i,:].flat) for i 
                                      in xrange(stoichiometric_matrix.shape[0])])

        logging.info("There are %d unique reactions" % unique_rows_S.shape[0])
        
        # for every unique row, calculate the average dG0_r of all the rows that
        # are the same reaction
        n_rows = data['dG0_r'].shape[0]
        n_unique_rows = unique_rows_S.shape[0]
        
        # full_data_mat will contain these columns: dG0, dG0_tag, dG0 - E[dG0], 
        # dG0_tag - E[dG0_tag], N
        # the averages are over the equivalence set of each reaction (i.e. the 
        # average dG of all the rows in NIST with that same reaction).
        # 'N' is the unique row number (i.e. the ID of the equivalence set)
        full_data_mat = np.zeros((n_rows, 5))
        full_data_mat[:, 0] = data['dG0_r'][:, 0]
        full_data_mat[:, 1] = data['dG0_r_tag'][:, 0]
        
        # unique_data_mat will contain these columns: E[dG0], E[dG0_tag],
        # std(dG0), std(dG0_tag), no. rows
        # there is exactly one row for each equivalence set (i.e. unique reaction)
        # no. rows holds the number of times this unique reaction appears in NIST
        unique_data_mat = np.zeros((n_unique_rows, 5))
        unique_sparse_reactions = []
        for i in xrange(n_unique_rows):
            # convert the rows of unique_rows_S to a list of sparse reactions
            sparse = dict([(int(cids_to_estimate[j]), unique_rows_S[i, j]) 
                           for j in pylab.find(unique_rows_S[i, :])])
            unique_sparse_reactions.append(sparse)

            # find the list of indices which are equal to row i in unique_rows_S
            diff = abs(stoichiometric_matrix - unique_rows_S[i,:])
            row_indices = pylab.find(pylab.sum(diff, 1) == 0)
            
            # take the mean and std of the dG0_r of these rows
            unique_data_mat[i, 0:2] = np.mean(full_data_mat[row_indices, 0:2], 0)
            unique_data_mat[i, 2:4] = np.std(full_data_mat[row_indices, 0:2], 0)
            unique_data_mat[i, 4]   = len(row_indices)
            full_data_mat[row_indices, 4] = i
            full_data_mat[row_indices, 2:4] = full_data_mat[row_indices, 0:2]
            for j in row_indices:
                full_data_mat[j, 2:4] -= unique_data_mat[i, 0:2]
                    
        # write a table that lists the variances of each unique reaction
        # before and after the reverse transform
        self.WriteUniqueReactionReport(unique_sparse_reactions, 
                                       unique_data_mat, full_data_mat)
        
        dG0 = unique_data_mat[:, 0:1]
        if use_anchors:
            # get a vector of anchored formation energies. one needs to be careful
            # to always use the most basic pseudoisomer (the one with the lowest nH)
            # because these are the forms used in the regression matrix
            anchor_dG0_f = np.zeros((cids_to_estimate.shape[0], 1))
    
            anchor_cols = []
            for cid in self.nist_anchors.GetAllCids():
                dG0_f = self.nist_anchors.cid2dG0_f[cid]
                nH = self.nist_anchors.cid2min_nH[cid]
                if cid not in self.cid2diss_table:
                    diss = DissociationTable(cid)
                    diss.min_dG0 = dG0_f
                    diss.min_nH = nH
                    diss.CalculateCharge()
                    self.cid2diss_table[cid] = diss
                else:
                    self.cid2diss_table[cid].min_dG0 = self.ConvertPseudoisomer(cid, dG0_f, nH)
                
                self.cid2pmap_dict[cid] = self.cid2diss_table[cid].GetPseudoisomerMap()
                
                if cid in cids_to_estimate:
                    c = pylab.find(cids_to_estimate == cid)[0]
                    anchor_cols.append(c)
                    anchor_dG0_f[c, 0] = self.cid2diss_table[cid].min_dG0

            # subtract the effect of the anchor compounds on the reverse-transformed
            # reaction energies.
            dG0 -= np.dot(unique_rows_S, anchor_dG0_f)
            
            # remove anchored compounds and compounds that do no appear in S
            # (since all the reactions that they are in were discarded).
            unique_rows_S = np.delete(unique_rows_S, anchor_cols, 1)
            cids_to_estimate = np.delete(cids_to_estimate, anchor_cols, 0)
        
        # numpy arrays contains a unique data type for integers and that
        # should be converted to the native type in python.
        cids_to_estimate = [int(x) for x in cids_to_estimate]
        return unique_rows_S, dG0, cids_to_estimate

    def ReactionVector2String(self, stoichiometric_vec, cids):
        nonzero_columns = pylab.find(abs(stoichiometric_vec) > 1e-10)
        gv = " + ".join(["%g %s (C%05d)" % (stoichiometric_vec[i], 
            self.kegg.cid2name(int(cids[i])), cids[i]) for i in nonzero_columns])
        return gv

    def FindKernel(self, S, cids, sparse=True):
        sparse_kernel = SparseKernel(S)
        logging.info("Regression matrix is %d x %d, with a nullspace of rank %d" % \
                     (S.shape[0], S.shape[1], len(sparse_kernel)))
        
        # Remove non-zero columns
        if False:
            nonzero_columns = pylab.find(np.sum(abs(S), 0))
            S = S[:, nonzero_columns]
            cids = [cids[i] for i in nonzero_columns]

        logging.info("Finding the kernel of the stoichiometric matrix")

        dict_list = []
        if not sparse:
            K = LinearRegression.FindKernel(S)
            for i in xrange(K.shape[0]):
                v_str = self.ReactionVector2String(K[i, :], cids)
                dict_list.append({'dimension':i, 'kernel vector':v_str})
        else:
            try:
                for i, v in enumerate(sparse_kernel):
                    v_str = self.ReactionVector2String(v, cids)
                    print i, ':', v_str
                    dict_list.append({'dimension':i, 'kernel vector':v_str})
            except SparseKernel.LinearProgrammingException as e:
                print "Error when trying to find a sparse kernel: " + str(e)
        self.html_writer.write_table(dict_list, ['dimension', 'kernel vector'])
    
    def ExportToTextFiles(self, S, dG0, cids):
        
        # export the raw data matrices to text files
        prefix = '../res/nist/regress_'
        np.savetxt(prefix + 'CID.txt', pylab.array(cids), fmt='%d', delimiter=',')
        np.savetxt(prefix + 'S.txt', S, fmt='%g', delimiter=',')
        np.savetxt(prefix + 'dG0.txt', dG0, fmt='%.2f', delimiter=',')
        
        for i in xrange(S.shape[0]):
            print i, self.ReactionVector2String(S[i, :], cids)
    
    def LinearRegression(self, S, dG0, cids, prior_thermodynamics=None):
        rankS = LinearRegression.Rank(S)
        logging.info("Regression matrix is %d x %d, with a nullspace of rank %d" % \
                     (S.shape[0], S.shape[1], S.shape[1]-rankS))
        est_dG0_f, kerA = LinearRegression.LeastSquares(S, dG0)
        est_dG0_r = np.dot(S, est_dG0_f)
        residuals = est_dG0_r - dG0
        rmse = np.sqrt(np.mean(residuals**2))
        logging.info("Regression results for reverse transformed data:")
        logging.info("N = %d, RMSE = %.1f" % (S.shape[0], rmse))
        logging.info("Kernel rank = %d" % (kerA.shape[0]))

        if prior_thermodynamics:
            # find the vector in the solution subspace which is closest to the 
            # prior formation energies
            delta_dG0_f = pylab.zeros((0, 1))
            indices_in_prior = []
            for i, cid in enumerate(cids):
                try:
                    pmap = prior_thermodynamics.cid2PseudoisomerMap(cid)
                    for p_nH, unused_z, p_nMg, dG0 in sorted(pmap.ToMatrix()):
                        if p_nMg == 0:
                            dG0_base = self.ConvertPseudoisomer(cid, dG0, p_nH)
                            difference = dG0_base - est_dG0_f[i, 0]
                            delta_dG0_f = np.vstack([delta_dG0_f, difference])
                            indices_in_prior.append(i)
                            break
                except MissingCompoundFormationEnergy:
                    continue
            
            v, _ = LinearRegression.LeastSquares(kerA.T[indices_in_prior,:], 
                        delta_dG0_f, reduced_row_echlon=False)
            est_dG0_f += np.dot(kerA.T, v)

        # copy the solution into the diss_tables of all the compounds,
        # and then generate their PseudoisomerMaps.
        for i, cid in enumerate(cids):
            self.cid2diss_table[cid].min_dG0 = est_dG0_f[i, 0]
            self.cid2pmap_dict[cid] = self.cid2diss_table[cid].GetPseudoisomerMap()

    def WriteUniqueReactionReport(self, unique_sparse_reactions, 
                                  unique_data_mat, full_data_mat):
        
        total_std = np.std(full_data_mat[:, 2:4], 0)
        
        fig = pylab.figure()
        pylab.plot(unique_data_mat[:, 2], unique_data_mat[:, 3], '.')
        pylab.xlabel("$\sigma(\Delta_r G^\circ)$")
        pylab.ylabel("$\sigma(\Delta_r G^{\'\circ})$")
        pylab.title('$\sigma_{total}(\Delta_r G^\circ) = %.1f$ kJ/mol, '
                    '$\sigma_{total}(\Delta_r G^{\'\circ}) = %.1f$ kJ/mol' % 
                    (total_std[0], total_std[1]))
        self.html_writer.embed_matplotlib_figure(fig, width=640, height=480)
        logging.info('std(dG0_r) = %.1f' % total_std[0])
        logging.info('std(dG\'0_r) = %.1f' % total_std[1])
        
        _mkdir('../res/nist/reactions')

        table_headers = ["Reaction", "#observations", "std(dG0)", "std(dG'0)", "analysis"]
        dict_list = []
        
        for i in xrange(len(unique_sparse_reactions)):
            logging.debug('Analysing unique reaction %03d: %s' %
                          (i, kegg_reaction.Reaction.write_full_reaction(unique_sparse_reactions[i])) )
            d = {}
            d["Reaction"] = self.kegg.sparse_to_hypertext(
                                unique_sparse_reactions[i], show_cids=False)
            d["std(dG0)"] = "%.1f" % unique_data_mat[i, 2]
            d["std(dG'0)"] = "%.1f" % unique_data_mat[i, 3]
            d["diff"] = unique_data_mat[i, 2] - unique_data_mat[i, 3]
            d["#observations"] = "%d" % unique_data_mat[i, 4]

            if d["diff"] > self.std_diff_threshold:
                link = "reactions/nist_reaction%03d.html" % i
                d["analysis"] = '<a href="%s">link</a>' % link
                reaction_html_writer = HtmlWriter(os.path.join('../res/nist', link))
                self.AnalyseSingleReaction(unique_sparse_reactions[i],
                                           html_writer=reaction_html_writer)
            else:
                d["analysis"] = ''
            dict_list.append(d)
        
        dict_list.sort(key=lambda x:x["diff"], reverse=True)
        self.html_writer.write_table(dict_list, table_headers)

    def AnalyseSingleReaction(self, sparse, html_writer=None):
        pylab.rcParams['text.usetex'] = False
        pylab.rcParams['legend.fontsize'] = 6
        pylab.rcParams['font.family'] = 'sans-serif'
        pylab.rcParams['font.size'] = 8
        pylab.rcParams['lines.linewidth'] = 2
        pylab.rcParams['lines.markersize'] = 5
        pylab.rcParams['figure.figsize'] = [8.0, 6.0]
        pylab.rcParams['figure.dpi'] = 100

        if not html_writer:
            html_writer = self.html_writer

        # gather all the measurements from NIST that correspond to this reaction
        nist_rows = self.nist.SelectRowsFromNist(sparse)
        
        html_writer.write('<p>\nShow observation table: ')
        div_id = html_writer.insert_toggle()
        html_writer.start_div(div_id)
        dict_list = []
        for nist_row_data in nist_rows:
            d = {}
            d['pH'] = nist_row_data.pH
            d['I'] = nist_row_data.I
            d['pMg'] = nist_row_data.pMg
            d['dG\'0_r'] = "%.2f" % nist_row_data.dG0_r
            d['T(K)'] = nist_row_data.T
            if nist_row_data.url:
                d['URL'] = '<a href="%s">link</a>' % nist_row_data.url
            else:
                d['URL'] = ''
            dict_list.append(d)
        html_writer.write_table(dict_list, headers=['T(K)', 'pH', 'I', 'pMg', 'dG\'0_r', 'URL'])
        html_writer.end_div()
        html_writer.write('</p>\n')

        # reverse transform the data
        data = self.ReverseTranformNistRows(nist_rows)
        
        hyper = self.kegg.sparse_to_hypertext(sparse, show_cids=False)
        html_writer.write('Reaction: %s</br>\n' % hyper)
        fig1 = pylab.figure()
        html_writer.write('Standard deviations:</br>\n<ul>\n')
        
        y_labels = ['$\Delta_r G^{\'\circ}$', '$\Delta_r G^{\circ}$']
        x_limits = {'pH' : (3, 12), 'I' : (0, 1), 'pMg' : (0, 10)}
        
        for j, y_axis in enumerate(['dG0_r_tag', 'dG0_r']):
            sigma = np.std(data[y_axis])
            html_writer.write("  <li>stdev(%s) = %.2g</li>" % (y_axis, sigma))
            for i, x_axis in enumerate(['pH', 'I', 'pMg']):
                pylab.subplot(2,3,i+3*j+1)
                pylab.plot(data[x_axis], data[y_axis], 'x')
                pylab.xlim(x_limits[x_axis])
                if j == 1:
                    pylab.xlabel(x_axis)
                if i == 0:
                    pylab.ylabel(y_labels[j])
        html_writer.write('</ul>\n')
        html_writer.embed_matplotlib_figure(fig1, width=640, height=480)
        
        # draw the response of the graph to pH, I and pMg:
        fig2 = pylab.figure()

        pH_range = np.arange(3, 12.01, 0.25)
        I_range = np.arange(0.0, 1.01, 0.05)
        pMg_range = np.arange(0.0, 10.01, 0.2)

        ddG_vs_pH = []
        for pH in pH_range:
            ddG = self.ReverseTransformReaction(sparse, pH=pH, I=default_I, 
                                                pMg=default_pMg, T=default_T)
            ddG_vs_pH.append(ddG)
        
        ddG_vs_I = []
        for I in I_range:
            ddG = self.ReverseTransformReaction(sparse, pH=default_pH, I=I, 
                                                pMg=default_pMg, T=default_T)
            ddG_vs_I.append(ddG)

        ddG_vs_pMg = []
        for pMg in pMg_range:
            ddG = self.ReverseTransformReaction(sparse, pH=default_pH, I=default_I, 
                                                pMg=pMg, T=default_T)
            ddG_vs_pMg.append(ddG)
        
        pylab.subplot(1, 3, 1)
        pylab.plot(pH_range, ddG_vs_pH, 'g-')
        pylab.xlabel('pH')
        pylab.ylabel('$\Delta_r G^{\'\circ} - \Delta_r G^{\circ}$')
        pylab.subplot(1, 3, 2)
        pylab.plot(I_range, ddG_vs_I, 'b-')
        pylab.xlabel('I')
        pylab.subplot(1, 3, 3)
        pylab.plot(pMg_range, ddG_vs_pMg, 'r-')
        pylab.xlabel('pMg')
        html_writer.write('</br>\n')
        html_writer.embed_matplotlib_figure(fig2, width=640, height=480)
        
    def ConvertPseudoisomer(self, cid, dG0, nH_from, nH_to=None):
        try:
            return self.cid2diss_table[cid].ConvertPseudoisomer(dG0, nH_from, nH_to)
        except KeyError:
            raise KeyError("Cannot find the pKas of C%05d (%s)" % \
                           (cid, self.kegg.cid2name(cid)))
    
    def ReverseTransformReaction(self, sparse, pH, I, pMg, T):
        """
            Calculates the difference between dG'0_r and dG0_r
        """
        return sum([coeff * self.ReverseTransformCompound(cid, pH, I, pMg, T) \
                    for cid, coeff in sparse.iteritems()])

    def ReverseTransformCompound(self, cid, pH, I, pMg, T):
        """
            Calculates the difference between dG'0_f and dG0_f
        """
        return self.cid2diss_table[cid].Transform(pH, I, pMg, T)

    def Nist_pKas(self):
        group_decomposer = GroupDecomposer.FromDatabase(self.db)
        cids_in_nist = set(self.nist.cid2count.keys())
        cids_with_pKa = set(self.cid2diss_table.keys())
        
        self.html_writer.write('CIDs with pKa: %d<br>\n' % len(cids_with_pKa))
        self.html_writer.write('CIDs in NIST: %d<br>\n' % len(cids_in_nist))
        self.html_writer.write('CIDs in NIST with pKas: %d<br>\n' % \
                          len(cids_in_nist.intersection(cids_with_pKa)))
        
        self.html_writer.write('All CIDs in NIST: <br>\n')
        self.html_writer.write('<table border="1">\n')
        self.html_writer.write('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>' % ("cid", "name", "count", "remark"))
        for cid, count in sorted(self.nist.cid2count.iteritems()):
            if cid not in cids_with_pKa:
                self.html_writer.write('<tr><td><a href="%s">C%05d<a></td><td>%s</td><td>%d</td><td>' % \
                    (self.kegg.cid2link(cid), cid, self.kegg.cid2name(cid), count))
                try:
                    mol = self.kegg.cid2mol(cid)
                    decomposition = group_decomposer.Decompose(mol, ignore_protonations=True, strict=True)
        
                    if len(decomposition.PseudoisomerVectors()) > 1:
                        self.html_writer.write('should have pKas')
                    else:
                        self.html_writer.write('doesn\'t have pKas')
                    self.html_writer.embed_molecule_as_png(
                        self.kegg.cid2mol(cid), 'png/C%05d.png' % cid)
                
                except Exception:
                    self.html_writer.write('cannot decompose')
                self.html_writer.write('</td></tr>\n')
        
        self.html_writer.write('</table>\n')

    def Calculate_pKa_and_pKMg(self, filename="../data/thermodynamics/dG0.csv"):
        cid2pmap = {}
        smiles_dict = {}
        
        for row in csv.DictReader(open(filename, 'r')):
            #smiles, cid, compound_name, dG0, unused_dH0, charge, hydrogens, Mg, use_for, ref, unused_assumption 
            name = "%s (z=%s, nH=%s, nMg=%s)" % (row['compound name'], row['z'], row['nH'], row['nMg'])
            logging.info('reading data for ' + name)
    
            if not row['dG0']:
                continue
    
            if (row['use for'] == "skip"):
                continue
                
            try:
                dG0 = float(row['dG0'])
            except ValueError:
                raise Exception("Invalid dG0: " + str(dG0))
    
            if (row['use for'] == "test"):
                pass
            elif (row['use for'] == "train"):
                pass
            else:
                raise Exception("Unknown usage flag: " + row['use for'])
    
            if row['cid']:
                cid = int(row['cid'])
                try:
                    nH = int(row['nH'])
                    z = int(row['z'])
                    nMg = int(row['nMg'])
                except ValueError:
                    raise Exception("can't read the data about %s" % (row['compound name']))
                cid2pmap.setdefault(cid, PseudoisomerMap())
                cid2pmap[cid].Add(nH, z, nMg, dG0)
    
            if row['smiles']:
                smiles_dict[cid, nH, z, nMg] = row['smiles']
            else: 
                smiles_dict[cid, nH, z, nMg] = ''
    
        #csv_writer = csv.writer(open('../res/pKa_from_dG0.csv', 'w'))
        
        self.self.html_writer.write('<table border="1">\n<tr><td>' + 
                          '</td><td>'.join(['cid', 'name', 'formula', 'nH', 'z', 'nMg', 'dG0_f', 'pKa', 'pK_Mg']) + 
                          '</td></tr>\n')
        for cid in sorted(cid2pmap.keys()):
            #step = 1
            for nH, z, nMg, dG0 in sorted(cid2pmap[cid].ToMatrix(), key=lambda x:(-x[2], -x[0])):
                pKa = cid2pmap[cid].GetpKa(nH, z, nMg)
                pK_Mg = cid2pmap[cid].GetpK_Mg(nH, z, nMg)
                self.self.html_writer.write('<tr><td>')
                self.self.html_writer.write('</td><td>'.join(["C%05d" % cid, 
                    self.kegg.cid2name(cid) or "?", 
                    self.kegg.cid2formula(cid) or "?", 
                    str(nH), str(z), str(nMg), 
                    "%.1f" % dG0, str(pKa), str(pK_Mg)]))
                #if not nMg and cid not in cid2pKa_list:
                #    csv_writer.writerow([cid, kegg.cid2name(cid), kegg.cid2formula(cid), step, None, "%.2f" % pKa, smiles_dict[cid, nH+1, z+1, nMg], smiles_dict[cid, nH, z, nMg]])
                #    step += 1
                self.self.html_writer.write('</td></tr>\n')
        self.self.html_writer.write('</table>\n')

    def ToDatabase(self):
        Thermodynamics.ToDatabase(self, self.db, 'nist_regression')

    def FromDatabase(self):
        if self.db.DoesTableExist('nist_regression'):
            Thermodynamics.FromDatabase(self, self.db, 'nist_regression')
        else:
            logging.warning('You should run nist_regression.py before trying to'
                            ' load the data from the database')
        
    def WriteDataToHtml(self):
        Thermodynamics.WriteDataToHtml(self, self.html_writer, self.kegg)
        
    def VerifyResults(self):
        return self.nist.verify_results(html_writer=self.html_writer, 
                                        thermodynamics=self)


def main():
    html_writer = HtmlWriter("../res/nist/regression.html")
    db = SqliteDatabase('../res/gibbs.sqlite')
    db_public = SqliteDatabase('../data/public_data.sqlite')
    nist_regression = NistRegression(db, html_writer)
    
    if False:
        html_writer.write("<h2>NIST pKa table:</h2>")
        nist_regression.Nist_pKas()
        #nist_regression.Calculate_pKa_and_pKMg()
    else:
        nist_regression.std_diff_threshold = 100.0
        nist_regression.nist.T_range = (298, 314)
        #nist_regression.nist.override_I = 0.25
        nist_regression.nist.override_pMg = 14.0
        S, dG0, cids = nist_regression.ReverseTransform(use_anchors=True)

        #nist_regression.ExportToTextFiles(S, dG0, cids)
        html_writer.write("<h2>NIST regression:</h2>")
        
        alberty = PsuedoisomerTableThermodynamics.FromDatabase(db_public, 'alberty_pseudoisomers')
        alberty.ToDatabase(db, 'alberty')
        nist_regression.LinearRegression(S, dG0, cids, prior_thermodynamics=alberty)
        nist_regression.ToDatabase()
        
        html_writer.write('<h3>Regression results:</h3>\n')
        html_writer.insert_toggle('regression')
        html_writer.start_div('regression')
        nist_regression.WriteDataToHtml()
        html_writer.end_div()
    
        html_writer.write('<h3>Reaction energies - Estimated vs. Observed:</h3>\n')
        html_writer.insert_toggle('verify')
        html_writer.start_div('verify')
        N, rmse = nist_regression.VerifyResults()
        html_writer.end_div()
        html_writer.write('</br>\n')
        
        logging.info("Regression results for observed data:")
        logging.info("N = %d, RMSE = %.1f" % (N, rmse))

        html_writer.write('<h3>Formation energies - Estimated vs. Alberty:</h3>\n')

        query = 'SELECT a.cid, a.nH, a.z, a.nMg, a.dG0_f, r.dG0_f ' + \
                'FROM alberty a, nist_regression r ' + \
                'WHERE a.cid=r.cid AND a.nH=r.nH AND a.nMg=r.nMg ' + \
                'AND a.anchor=0 ORDER BY a.cid,a.nH'
        
        data = np.zeros((0, 2))
        fig = pylab.figure()
        pylab.hold(True)
        for row in db.Execute(query):
            cid, unused_nH, z, unused_nMg, dG0_a, dG0_r = row
            name = nist_regression.kegg.cid2name(cid)
            x = (dG0_a + dG0_r)/2
            y = dG0_a - dG0_r
            pylab.text(x, y, "%s [%d]" % (name, z), fontsize=5, rotation=20)
            data = np.vstack([data, (x,y)])

        pylab.plot(data[:,0], data[:,1], '.')
        html_writer.embed_matplotlib_figure(fig, width=640, height=480)

        nist_regression.FindKernel(S, cids, sparse=True)

    html_writer.close()
    
if (__name__ == "__main__"):
    main()
