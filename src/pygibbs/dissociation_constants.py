import csv, logging, sys
from kegg import Kegg
from toolbox.database import SqliteDatabase
from pygibbs.thermodynamic_constants import R, default_T, dG0_f_Mg, debye_huckel,\
    default_I, default_pH, default_pMg
import numpy as np
from toolbox.util import log_sum_exp
from pygibbs.pseudoisomer import PseudoisomerMap
from pygibbs.pseudoisomers_data import PseudoisomerEntry
from pygibbs.kegg_errors import KeggParseException
from pygibbs.nist import Nist
from pygibbs.thermodynamics import PsuedoisomerTableThermodynamics
from toolbox.html_writer import HtmlWriter
from toolbox.molecule import Molecule, OpenBabelError
from pygibbs.kegg_reaction import Reaction

class MissingDissociationConstantError(Exception):
    pass

class DissociationConstants(object):
    
    def __init__(self):
        self.cid2DissociationTable = {}
    
    @staticmethod
    def FromFile(filename='../data/thermodynamics/dissociation_constants.csv'):
        """
            Parses a CSV file that contains pKa and pKMg data for many compounds
            and returns a dictionary of their DissociationTables, where the key
            is the CID.
        """
        diss = DissociationConstants()

        for i, row in enumerate(csv.DictReader(open(filename, 'r'))):
            try:
                if not row['cid']:
                    continue # without a CID we cannot match this to the dG0 table
                cid = int(row['cid'])
                diss.cid2DissociationTable.setdefault(cid, DissociationTable(cid))
                logging.debug("Parsing row #%d, compound C%05d" % (i, cid))
    
                nH_below = int(row['nH_below'])
                nH_above = int(row['nH_above'])
                nMg_below = int(row['nMg_below'])
                nMg_above = int(row['nMg_above'])
                if row['smiles_below']:
                    mol_below = Molecule.FromSmiles(row['smiles_below'])
                else:
                    mol_below = None
                    
                if row['smiles_above']:
                    mol_above = Molecule.FromSmiles(row['smiles_above'])
                else:
                    mol_above = None
                
                ref = row['ref']
                T = float(row['T'] or default_T)
                diss.UpdateMinNumHydrogens(cid, nH_above)
    
                if row['type'] == 'acid-base':
                    pKa = float(row['pK'])
                    if nMg_below != nMg_above:
                        raise Exception('C%05d has different nMg below and above '
                                        'the pKa = %.1f' % (cid, pKa))
                    diss.AddpKa(cid, pKa, nH_below, nH_above, nMg_below,
                                ref, T, mol_below, mol_above)
                elif row['type'] == 'Mg':
                    pKMg = float(row['pK'])
                    if nH_below != nH_above:
                        raise Exception('C%05d has different nH below and above '
                                        'the pK_Mg = %.1f' % pKMg)
                    try:
                        diss.AddpKMg(cid, pKMg, nMg_below, nMg_above, nH_below,
                                     ref, T, mol_below, mol_above)
                    except Exception, e:
                        raise Exception("In C%05d: %s" % (cid, str(e)))
                elif row['pK']:
                    raise ValueError('The row about C%05d has a pK although it is not "acid-base" nor "Mg"' % cid)
                elif nMg_below != nMg_above:
                    raise ValueError('The row about C%05d has different nMgs although it is not "Mg"' % cid)
                elif nH_below != nH_above:
                    raise ValueError('The row about C%05d has different nHs although it is not "acid-base"' % cid)
                elif mol_below is not None:
                    diss.SetOnlyPseudoisomer(cid, mol_below, nMg_below)
                else:
                    diss.GetDissociationTable(cid).min_nH = nH_below
                    
            except ValueError as e:
                raise ValueError("At row %i: %s" % (i, str(e)))
            except TypeError as e:
                raise TypeError("At row %i: %s" % (i, str(e)))
        
        diss.CalculateAllCharges()
        return diss
    
    @staticmethod
    def FromChemAxon(cid2mol=None, html_writer=None):
        kegg = Kegg.getInstance()
        diss = DissociationConstants()
        if cid2mol is None:
            cid2mol = dict([(cid, None) for cid in kegg.get_all_cids()])
        
        for cid, mol in sorted(cid2mol.iteritems()):
            logging.info("Using ChemAxon to find the pKa values for %s - C%05d" %
                         (kegg.cid2name(cid), cid))
            if html_writer:
                html_writer.write('<h2>%s - C%05d</h2>\n' %
                                  (kegg.cid2name(cid), cid))
            # if this CID is not assigned to a Molecule, use the KEGG database
            # to create a Molecule for it.
            if mol is None:
                try:
                    mol = kegg.cid2mol(cid)
                except KeggParseException:
                    continue

            diss_table = mol.GetPseudoisomerMap()
            diss.cid2DissociationTable[cid] = diss_table
            if diss_table and html_writer:
                diss_table.WriteToHTML(html_writer)
                html_writer.write('</br>\n')
        return diss
    
    @staticmethod
    def FromDatabase(db, table_name='dissociation_constants'):
        diss = DissociationConstants()

        for row in db.DictReader(table_name):
            cid = row['cid']
            if row['nH_below'] is None:
                diss.cid2DissociationTable[cid] = None
            else:
                if cid not in diss.cid2DissociationTable:
                    diss.cid2DissociationTable[cid] = DissociationTable(cid)
                try:
                    diss.cid2DissociationTable[cid].UpdateDatabaseRow(row)
                except OpenBabelError as e:
                    raise Exception("Cannot read this row from the database: "
                                    + str(row) + '\n' + str(e))
                    
        diss.CalculateAllCharges()
        return diss
    
    def AddpKa(self, cid, pKa, nH_below, nH_above, nMg=0, ref="", T=default_T, 
               mol_below=None, mol_above=None):
        diss_table = self.GetDissociationTable(cid)
        diss_table.AddpKa(pKa, nH_below, nH_above, nMg,
                          ref, T, mol_below, mol_above)
        
    def AddpKMg(self, cid, pKMg, nMg_below, nMg_above, nH, ref="", T=default_T, 
                mol_below=None, mol_above=None):
        diss_table = self.GetDissociationTable(cid)
        diss_table.AddpKMg(pKMg, nMg_below, nMg_above, nH, 
                          ref, T, mol_below, mol_above)
        
    def SetOnlyPseudoisomer(self, cid, mol, nMg=0):
        diss_table = self.GetDissociationTable(cid)
        diss_table.SetOnlyPseudoisomer(mol, nMg=nMg)
    
    def UpdateMinNumHydrogens(self, cid, min_nH):
        """
            Sets the minimal number of hydrogen atoms for a specified CID.
            
            If the value is not lower than the previously provided min_nH, the
            lower value is kept unchanged. 
        """
        diss_table = self.GetDissociationTable(cid)
        diss_table.UpdateMinNumHydrogens(min_nH)
            
    def GetDissociationTable(self, cid, create_if_missing=True):
        if cid not in self.cid2DissociationTable and create_if_missing:
            try:
                kegg = Kegg.getInstance()
                mol = kegg.cid2mol(cid)
                diss_table = DissociationTable.CreateUsingChemaxon(mol)
            except KeggParseException:
                diss_table = None
            self.cid2DissociationTable[cid] = diss_table 
        
        return self.cid2DissociationTable.get(cid, None)
        
    def GetMol(self, cid, nH, nMg=0):
        diss_table = self.GetDissociationTable(cid)
        if diss_table is None:
            return None
        else:
            return diss_table.GetMol(nH, nMg)
    
    def CalculateAllCharges(self):
        for diss_table in self.cid2DissociationTable.values():
            if diss_table is not None:
                diss_table.CalculateCharge()
    
    def ToDatabase(self, db, table_name):
        """
            Load the data regarding pKa values according to KEGG compound IDs.
        """
        kegg = Kegg.getInstance()
        
        db.CreateTable(table_name, """
            cid INT, name TEXT, 
            nH_below INT, nH_above INT, 
            nMg_below INT, nMg_above INT, 
            mol_below TEXT, mol_above TEXT, 
            ddG REAL, ref TEXT""")
        
        for cid in sorted(self.cid2DissociationTable.keys()):
            name = kegg.cid2name(cid)
            diss_table = self.cid2DissociationTable[cid]
            if diss_table is None:
                db.Insert(table_name, [cid, name] + [None] * 8)
            else:
                for row in diss_table.ToDatabaseRow(db, table_name):
                    db.Insert(table_name, [cid, name] + row)

        db.Commit()

    def GetAllCids(self):
        return set(self.cid2DissociationTable.keys())

    def ReverseTranformNistRows(self, nist_rows, cid2nH=None, assume_no_pka_by_default=False):
        all_cids = set()
        for nist_row_data in nist_rows:
            all_cids.update(nist_row_data.GetAllCids())
        all_cids = list(all_cids)
        
        data = {}
        data['dG0_r_tag'] = [] # the transformed free energy of the reactions dG'0_r
        data['dG0_r'] = [] # the chemical free energy of the reactions dG0_r
        data['ddG0_r'] = [] # dG'0_r - dG0_r  (which is only a function of the conditions and pKas)
        data['pH'] = []
        data['I'] = []
        data['pMg'] = []
        data['T'] = []
        data['S'] = np.zeros((0, len(all_cids))) # stoichiometric matrix
        data['nist_rows'] = [] # the index of the corresponding row in nist_rows
        
        for nist_row_data in nist_rows:
            # check that all participating compounds have a known pKa
            try:
                ddG = self.ReverseTransformReaction(nist_row_data.reaction, 
                    nist_row_data.pH, nist_row_data.I, nist_row_data.pMg,
                    nist_row_data.T, cid2nH=cid2nH)
            except MissingDissociationConstantError:
                logging.debug('A reaction contains compounds with missing pKa '
                              'values: ' + str(nist_row_data.reaction))
                continue
            
            data['dG0_r_tag'].append(nist_row_data.dG0_r)
            data['pH'].append(nist_row_data.pH)
            data['I'].append(nist_row_data.I)
            data['pMg'].append(nist_row_data.pMg)
            data['T'].append(nist_row_data.T)
            data['nist_rows'].append(nist_row_data)
            data['ddG0_r'].append(ddG)
            data['dG0_r'].append(nist_row_data.dG0_r - ddG)
            
            # convert the reaction's sparse representation to a row vector
            stoichiometric_row = np.zeros((1, len(all_cids)))
            for cid, coeff in nist_row_data.reaction.iteritems():
                stoichiometric_row[0, all_cids.index(cid)] = coeff
            data['S'] = np.vstack([data['S'], stoichiometric_row])
        
        # remove the columns that are all-zeros in S
        nonzero_columns = np.sum(abs(data['S']), 0).nonzero()[0]
        data['S'] = data['S'][:, nonzero_columns]
        data['cids_to_estimate'] = [all_cids[i] for i in nonzero_columns]
        
        return data
    
    def ReverseTransformReaction(self, reaction, pH, I, pMg, T, cid2nH=None):
        """
            Calculates the difference between dG'0_r and dG0_r
        """
        ddG0 = 0
        for cid, coeff in reaction.iteritems():
            diss_table = self.GetDissociationTable(cid)
            if diss_table is None:
                # probably a compound without an implicit formula
                raise MissingDissociationConstantError
            elif not cid2nH:
                ddG0 += coeff * diss_table.GetDeltaDeltaG0(pH, I, pMg, T)
            else:
                ddG0 += coeff * diss_table.GetDeltaDeltaG0(pH, I, pMg, T, nH=cid2nH[cid])
        
        return ddG0
        
    def GetPseudoisomerMap(self, cid):
        return self.GetDissociationTable(cid).GetPseudoisomerMap()
    
    def ConvertPseudoisomer(self, cid, dG0, nH_from, 
                            nH_to=None, nMg_from=0, nMg_to=0):
        diss = self.GetDissociationTable(cid)
        if nH_to == None:
            nH_to = diss.min_nH
        return diss.ConvertPseudoisomer(dG0, nH_from, nH_to, nMg_from, nMg_to)
        
    def Transform(self, cid, pH, I, pMg, T):
        return self.GetDissociationTable(cid).Transform(pH, I, pMg, T)

    def GetMostAbundantMol(self, cid, pH, I, pMg, T):
        diss_table = self.GetDissociationTable(cid)
        if diss_table is None:
            return None
        nH, nMg = diss_table.GetMostAbundantPseudoisomer(pH, I, pMg, T)
        return self.GetMol(cid, nH, nMg)
        

###############################################################################


class DissociationTable(object):
    
    def __init__(self, cid=None):
        # ddGs is a dictionary whose keys are 4-tuples of (nH_above, nH_below, nMg_above, nMg_below)
        # and the values are pairs of (ddG0, reference)
        self.ddGs = {}
        
        # mol_dict is a dictionary from pairs of (nH, nMg) to Molecule objects
        # describing the corresponding pseudoisomer
        self.mol_dict = {}
        
        self.cid = cid
        self.min_nH = None # the nH of the most basic pseudoisomer
        self.min_charge = None # the charge of the most basic pseudoisomer
        self.min_dG0 = 0 # the dG0 of the most basic pseudoisomer

    def __len__(self):
        return len(self.ddGs)

    def __str__(self):
        T = default_T
        s = "Base   nH=%d nMg=0 z=%d : dG0=%.1f kJ/mol\n" % \
            (self.min_nH, self.min_charge, self.min_dG0)
        for key in sorted(self.ddGs.keys()):
            nH_above, nH_below, nMg_above, nMg_below = key
            ddG, _ref = self.ddGs[key]
            if nH_above != nH_below:
                pKa = -ddG / (R * T * np.log(10))
                s += "pKa (nH=%d -> nH=%d) : %.1f\n" % \
                    (nH_above, nH_below, pKa)
            elif nMg_above != nMg_below:
                pKMg = (-ddG + dG0_f_Mg) / (R * T * np.log(10))
                s += "pKMg (nMg=%d -> nMg=%d) : %.1f\n" % \
                    (nMg_above, nMg_below, pKMg)

        for (nH, nMg), mol in self.mol_dict.iteritems():
            s += "Pseudoisomer nH=%d nMg=%d : %s\n" % (nH, nMg, mol.ToSmiles())
        
        return s

    def WriteToHTML(self, html_writer, T=default_T):
        dict_list = []
        if not self.ddGs:
            nH = self.min_nH
            nMg = 0
            svg = self.GetSVG(nH, nMg)
            ddG = 0.0
            ref = ""
            d = {'nH below':nH, 'nH above':nH,
                 'nMg below':nMg, 'nMg above':nMg,
                 'ddG0':'%.1f' % ddG, 'reference':ref,
                 'species below':svg, 'species above':svg}
            dict_list.append(d)
        else:
            for (nH_above, nH_below, nMg_above, nMg_below), (ddG, ref) in self.ddGs.iteritems():
                d = {'nH below':nH_below, 'nH above':nH_above,
                     'nMg below':nMg_below, 'nMg above':nMg_above,
                     'ddG0':'%.1f' % ddG, 'reference':ref}
                if nH_below == nH_above+1:
                    d['pK<sub>a</sub>'] = -ddG / (R * T * np.log(10))
                elif nMg_below == nMg_above+1:
                    d['pK<sub>Mg</sub>'] = (-ddG + dG0_f_Mg) / (R * T * np.log(10))
                d['species below'] = self.GetSVG(nH_below, nMg_below)
                d['species above'] = self.GetSVG(nH_above, nMg_above)
                dict_list.append(d)
        dict_list.sort(key=lambda(k):(k['nH below'], k['nMg below']))
        html_writer.write_table(dict_list, headers=['nH below', 'nH above', 
            'nMg below', 'nMg above', 'species below', 'species above',
            'ddG0', 'pK<sub>a</sub>', 'pK<sub>Mg</sub>', 'reference'])        

    def __iter__(self):
        return self.ddGs.__iter__()
    
    def GetMol(self, nH=None, nMg=0):
        if nH == None:
            nH = self.min_nH
        return self.mol_dict.get((nH, nMg), None)
    
    def GetSVG(self, nH=None, nMg=0):
        mol = self.GetMol(nH, nMg)
        if mol is not None:
            return mol.ToSVG()
        else:
            return ""
    
    def GetMolString(self, nH=None, nMg=0):
        mol = self.GetMol(nH, nMg)
        if mol is not None:
            return mol.ToSmiles()
        else:
            return None
        
    def SetMolString(self, nH, nMg, s):
        self.UpdateMinNumHydrogens(nH)
        if s is not None:
            mol = Molecule.FromSmiles(str(s))
            self.mol_dict[nH, nMg] = mol
    
    def ToDatabaseRow(self, db, table_name):
        """
            Return:
                A list of rows to insert into the database
        """
        res = []
        if not self.ddGs:
            nH = self.min_nH
            nMg = 0
            mol_str = self.GetMolString(nH, nMg)
            ddG = 0.0
            ref = ""
            res.append([nH, nH, nMg, nMg, mol_str, mol_str, ddG, ref])
        else:
            for key in sorted(self.ddGs.keys()):
                nH_above, nH_below, nMg_above, nMg_below = key
                mol_below = self.GetMolString(nH_below, nMg_below)
                mol_above = self.GetMolString(nH_above, nMg_above)
                ddG, ref = self.ddGs[key]
                res.append([nH_below, nH_above, nMg_below, nMg_above, 
                            mol_below, mol_above, ddG, ref])
        return res

    def UpdateDatabaseRow(self, row):
        (nH_below, nMg_below) = (row['nH_below'], row['nMg_below'])
        (nH_above, nMg_above) = (row['nH_above'], row['nMg_above'])
        self.SetMolString(nH_above, nMg_above, row['mol_above'])
        self.SetMolString(nH_below, nMg_below, row['mol_below'])

        if (nH_below, nMg_below) == (nH_above, nMg_above):
            return

        key = (nH_above, nH_below, nMg_above, nMg_below)
        self.ddGs[key] = (row['ddG'], row['ref'])

    def AddpKa(self, pKa, nH_below, nH_above, nMg=0, ref="", T=default_T, 
               mol_below=None, mol_above=None):
        if nH_below != nH_above+1:
            raise Exception('A H+ dissociation constant (pKa) has to represent an '
                            'increase of exactly one hydrogen: nH_below = %d, nH_above = %d' %
                            (nH_below, nH_above))
        
        ddG0 = R * T * np.log(10) * pKa
        key = (nH_above, nH_below, nMg, nMg)
        self.ddGs[key] = (-ddG0, ref) # adding H+ decreases dG0
        if mol_above is not None:
            self.mol_dict[nH_above, nMg] = mol_above
        if mol_below is not None:
            self.mol_dict[nH_below, nMg] = mol_below
        self.UpdateMinNumHydrogens(nH_above)
        
    def AddpKMg(self, pKMg, nMg_below, nMg_above, nH, ref="", T=default_T, 
                mol_below=None, mol_above=None):
        if nMg_below != nMg_above+1:
            raise Exception('A Mg+2 dissociation constant (pK_Mg) has to represent an '
                            'increase of exactly one magnesium ion: nMg_below = %d, nMg_above = %d' %
                            (nMg_below, nMg_above))
        ddG0 = R * T * np.log(10) * pKMg - dG0_f_Mg
        key = (nH, nH, nMg_above, nMg_below)
        self.ddGs[key] = (-ddG0, ref) # adding Mg+2 decreases dG0
        if mol_above is not None:
            self.mol_dict[nH, nMg_above] = mol_above
        if mol_below is not None:
            self.mol_dict[nH, nMg_below] = mol_below
        self.UpdateMinNumHydrogens(nH)
    
    def SetOnlyPseudoisomer(self, mol, nMg=0):
        """
            For compound which have no known pKa or pKMg, this method can be used
            to set the parameters of the only pseudoisomer.
        """
        if len(self.ddGs):
            raise ValueError("You tried to set the only-pseudoisomer of a compound that has pKas/pKMgs")
        nH, z = mol.GetHydrogensAndCharge()
        self.mol_dict[nH, nMg] = mol
        self.min_nH = nH
        if z is not None:
            self.SetCharge(nH, z, nMg)
    
    def UpdateMinNumHydrogens(self, min_nH):
        if not self.min_nH or self.min_nH > min_nH: 
            self.min_nH = min_nH

    def GetSingleStep(self, nH_from, nH_to, nMg_from, nMg_to):
        if nH_from == nH_to and nMg_from == nMg_to:
            return 0, None
        
        if nH_from != nH_to and nMg_from != nMg_to:
            raise Exception('A dissociation constant can either represent a'
                ' change in hydrogens or in magnesiums, but not both')
        
        try:
            if nMg_to == nMg_from+1 or nH_to == nH_from+1:
                ddG0, ref = self.ddGs[nH_from, nH_to, nMg_from, nMg_to]
                return ddG0, ref
            if nMg_from == nMg_to+1:
                ddG0, ref = self.ddGs[nH_from, nH_to, nMg_to, nMg_from]
                return -ddG0, ref
            if nH_from == nH_to+1:
                ddG0, ref = self.ddGs[nH_to, nH_from, nMg_from, nMg_to]
                return -ddG0, ref
        except KeyError:
            raise MissingDissociationConstantError(
                'The dissociation constant for C%05d: (nH=%d,nMg=%d) -> '
                '(nH=%d,nMg=%d) is missing' % (self.cid, nH_from, nMg_from, nH_to, nMg_to))

        raise Exception('A dissociation constant can either represent a'
                ' change in only one hydrogen or magnesium')
    
    def ConvertPseudoisomer(self, dG0, nH_from, nH_to=None, nMg_from=0, nMg_to=0):
        if not nH_to:
            nH_to = self.min_nH
        
        pdata = PseudoisomerEntry(net_charge=0, hydrogens=nH_from, 
            magnesiums=nMg_from, smiles="", dG0=dG0)
        comp = self.ConvertPseudoisomerEntry(pdata, nH_to, nMg_to)
        return comp.dG0
    
    def ConvertPseudoisomerEntry(self, pdata, nH_to, nMg_to):
        """
            Returns the difference in dG0 between any two pseudoisomers.
        """
        nH_from = pdata.hydrogens
        nMg_from = pdata.magnesiums
        
        step_list = []
        
        # first remove all Mgs from the original
        step_list += [self.GetSingleStep(nH_from, nH_from, nMg, nMg-1)
                      for nMg in xrange(nMg_from, 0, -1)]

        # then change the nH to fit the target (using only species without Mg)
        if nH_from < nH_to:
            step_list += [self.GetSingleStep(nH, nH+1, 0, 0)
                          for nH in xrange(nH_from, nH_to)]
        elif nH_from > nH_to:
            step_list += [self.GetSingleStep(nH, nH-1, 0, 0)
                          for nH in xrange(nH_from, nH_to, -1)]
        
        # finally add back all the Mg in the target
        step_list += [self.GetSingleStep(nH_to, nH_to, nMg, nMg+1)
                      for nMg in xrange(0, nMg_to)]
        
        
        total_ddG0 = sum([ddG0 for ddG0, _ref in step_list])
        total_ref = ';'.join([(ref or "") for _ddG0, ref in step_list])
        
        comp = pdata.Clone()
        comp.dG0 += total_ddG0
        comp.ref += ';' + total_ref
        comp.smiles = ''
        comp.hydrogens = nH_to
        comp.magnesiums = nMg_to
        comp.net_charge += (nH_to - nH_from) + 2 * (nMg_to - nMg_from)
        
        return comp
    
    def SetFormationEnergyByNumHydrogens(self, dG0, nH, nMg=0):
        """ Uses the value of any pseudoisomer to set the base value of dG0 """
        self.min_dG0 = self.ConvertPseudoisomer(dG0, nH_from=nH, 
            nH_to=self.min_nH, nMg_from=nMg, nMg_to=0)
        
    def SetFormationEnergyByCharge(self, dG0, charge, nMg=0):
        """ Uses the value of any pseudoisomer to set the base value of dG0 """
        nH = self.min_nH + (charge - self.min_charge)
        self.SetFormationEnergyByNumHydrogens(dG0, nH, nMg)
    
    def SetTransformedFormationEnergy(self, dG0_tag, pH, I, pMg, T):
        """ Sets the min_dG0 according to a transformed formation energy. """
        self.min_dG0 += dG0_tag - self.Transform(pH, I, pMg, T)
    
    def CalculateCharge(self):
        """ Calculate the charge for the most basic species """
        # get the charge and nH of the default pseudoisomer in KEGG:
        kegg = Kegg.getInstance()
        nH_z_pair = kegg.cid2nH_and_charge(self.cid)
        if nH_z_pair:
            nH, z = nH_z_pair
            self.min_charge = z + (self.min_nH - nH)
        else:
            self.min_charge = 0
            
    def SetCharge(self, nH, z, nMg=0):
        self.min_charge = z + (self.min_nH - nH) - 2 * nMg
        
    def GenerateAll(self):
        if self.min_charge == None:
            raise Exception('The minimal charge has to be set before generating'
            ' all psuedoisomers')
        if self.min_dG0 == None:
            raise Exception('The base formation energy has to be set before generating'
            ' all psuedoisomers')
            
        pdata = PseudoisomerEntry(net_charge=self.min_charge, hydrogens=self.min_nH,
            magnesiums=0, smiles="", dG0=self.min_dG0, cid=self.cid)
        return self.GenerateAllPseudoisomerEntries(pdata)
    
    def GenerateAllPseudoisomerEntries(self, pdata):
        # Having nMg=0 is not necessary but makes everything easier,
        # therefore I assume it is.
        if pdata.magnesiums != 0:
            raise ValueError("Must start with nMg=0 when generating all pseudoisomers")
        
        pseudoisomers = {}
        pseudoisomers[pdata.hydrogens, pdata.magnesiums] = pdata.Clone()

        for (nH_above, nH_below, nMg_above, nMg_below), (_ddG0, ref) in self.ddGs.iteritems():
            if nH_below == nH_above + 1: # this is a pKa
                nMg = nMg_below # it doesn't matter which, since nMg_below == nMg_above
                if nH_below > pdata.hydrogens: 
                    nH = nH_below # creating a pseudoisomer with more nH than pdata
                elif nH_above < pdata.hydrogens:
                    nH = nH_above # creating a pseudoisomer with less nH than pdata
            elif nMg_below == nMg_above + 1:
                nH = nH_below # it doesn't matter which, since nH_below == nH_above
                nMg = nMg_below # since we always start from nMg=0 and go up
                    
            pseudoisomers[nH, nMg] = self.ConvertPseudoisomerEntry(pdata, nH, nMg)
            pseudoisomers[nH, nMg].ref = ref
            
        return pseudoisomers.values()

    def GetTransformedDeltaGs(self, pH, I, pMg, T, nH=None, nMg=0):
        """
            Return:
                a list of the pseudoisomers and their transformed dG.
                each member of the list is a tuple: (nH, z, nMg, dG0')
            
            Note:
                Set the dG0 of one the pseudoisomer [nH, nMg] to 0.
        """
        nH = nH or self.min_nH
        pdata = PseudoisomerEntry(net_charge=(self.min_charge + nH - self.min_nH),
                                  hydrogens=nH, magnesiums=nMg, smiles="", dG0=0)
        
        pseudoisomer_matrix = []
        for pseudoisomer in self.GenerateAllPseudoisomerEntries(pdata):
            ps_nH = pseudoisomer.hydrogens
            ps_nMg = pseudoisomer.magnesiums
            ps_z = pseudoisomer.net_charge
            ps_dG0 = pseudoisomer.dG0
            
            DH = debye_huckel(I)
            dG0_tag = ps_dG0 + \
                      ps_nMg * (R*T*np.log(10)*pMg - dG0_f_Mg) + \
                      ps_nH  * (R*T*np.log(10)*pH + DH) - (ps_z**2) * DH
            pseudoisomer_matrix.append((ps_nH, ps_z, ps_nMg, dG0_tag))
        
        pseudoisomer_matrix.sort(key=lambda(x):x[3])
        return pseudoisomer_matrix
     
    def GetDeltaDeltaG0(self, pH, I, pMg, T, nH=None, nMg=0):
        """
            Return:
                the transformed ddG0 = dG0'_f - dG0_f

            Note:
                assume that the dG0_f of one of the psuedoisomers
                (according to the given nH) is 0
        """
        nH = nH or self.min_nH
        pseudoisomer_matrix = self.GetTransformedDeltaGs(pH, I, pMg, T, nH=nH)
        ddG0_f = -R * T * log_sum_exp([dG0_tag / (-R*T) for (_nH, _z, _nMg, dG0_tag) in pseudoisomer_matrix])
        return ddG0_f
    
    def GetMostAbundantPseudoisomer(self, pH, I, pMg, T):
        pseudoisomer_matrix = self.GetTransformedDeltaGs(pH, I, pMg, T)
        pseudoisomer_matrix.sort(key=lambda(x):x[3])
        nH, _z, nMg, _dG0_tag = pseudoisomer_matrix[0] # return the psuedoisomer with the smallest dG0_tag
        return (nH, nMg)
    
    def GetMostAbundantMol(self, pH, I, pMg, T):
        nH, nMg = self.GetMostAbundantPseudoisomer(pH, I, pMg, T)
        return self.GetMol(nH, nMg)
    
    def Transform(self, pH, I, pMg, T):    
        return self.min_dG0 + self.GetDeltaDeltaG0(pH, I, pMg, T, nH=self.min_nH)
    
    def GetPseudoisomerMap(self):
        pmap = PseudoisomerMap()
        for pdata in self.GenerateAll():
            pmap.Add(nH=pdata.hydrogens, z=pdata.net_charge, 
                     nMg=pdata.magnesiums, dG0=pdata.dG0,
                     ref=pdata.ref)
        return pmap

###############################################################################

if __name__ == '__main__':
    db = SqliteDatabase("../res/gibbs.sqlite")
    kegg = Kegg.getInstance()
    html_writer = HtmlWriter("../res/dissociation_constants.html")

    if False:
        # copy the data from the CSV file to the database
        dissociation_csv = DissociationConstants.FromFile()
        dissociation_csv.ToDatabase(db, 'dissociation_constants')
    
    if True:
        cid2smiles = {}
        # override aromatic compounds
        cid2smiles[2] = "Nc1ncnc2n(cnc12)C1OC(COP(O)(=O)OP(O)(=O)OP(O)(O)=O)C(O)C1O" # ATP 
        cid2smiles[3] = "NC(=O)c1ccc[n+](c1)C1OC(COP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2cnc3c(N)ncnc23)C(O)C1O" # NAD+
        cid2smiles[4] = "NC(=O)C1=CN(C=CC1)C1OC(COP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2cnc3c(N)ncnc23)C(O)C1O" # NADH
        cid2smiles[5] = "NC(=O)C1=CN(C=CC1)C1OC(COP(O)(=O)OP(O)(=O)OCC2OC(C(OP(O)(O)=O)C2O)n2cnc3c(N)ncnc23)C(O)C1O" # NADPH
        cid2smiles[6] = "NC(=O)c1ccc[n+](c1)C1OC(COP(O)(=O)OP(O)(=O)OCC2OC(C(OP(O)(O)=O)C2O)n2cnc3c(N)ncnc23)C(O)C1O" # MADP+
        cid2smiles[8] = "Nc1ncnc2n(cnc12)C1OC(COP(O)(=O)OP(O)(O)=O)C(O)C1O" # ADP
        cid2smiles[10] = "CC(C)(COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12)C(O)C(=O)NCCC(=O)NCCS" # CoA
        cid2smiles[20] = "Nc1ncnc2n(cnc12)C1OC(COP(O)(O)=O)C(O)C1O" # AMP
        cid2smiles[29] = "OCC1OC(OP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2ccc(=O)[nH]c2=O)C(O)C(O)C1O" # UDP-glucose
        cid2smiles[35] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(COP(O)(=O)OP(O)(O)=O)C(O)C1O" # GDP
        cid2smiles[44] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(COP(O)(=O)OP(O)(=O)OP(O)(O)=O)C(O)C1O" # GTP
        cid2smiles[52] = "OCC1OC(OP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2ccc(=O)[nH]c2=O)C(O)C(O)C1O" # UDP-galactose
        cid2smiles[55] = "Nc1ccn(C2OC(COP(O)(O)=O)C(O)C2O)c(=O)n1" # CMP
        cid2smiles[64] = "NC(CCC(=O)N)C(O)=O" # L-glutamine
        cid2smiles[75] = "OC1C(COP(O)(=O)OP(O)(=O)OP(O)(O)=O)OC(C1O)n1ccc(=O)[nH]c1=O" # UTP
        cid2smiles[78] = "NC(Cc1c[nH]c2ccccc12)C(O)=O" # L-tryptophan
        cid2smiles[79] = "NC(Cc1ccccc1)C(O)=O" # phenylalanine        
        cid2smiles[81] = "OC1C(COP(O)(=O)OP(O)(=O)OP(O)(O)=O)OC(C1O)n1cnc2c1nc[nH]c2=O" # ITP
        cid2smiles[82] = "NC(Cc1ccc(O)cc1)C(O)=O" # L-tyrosine
        cid2smiles[91] = "CC(C)(COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12)C(O)C(=O)NCCC(=O)NCCSC(=O)CCC(O)=O" # succinyl-CoA
        cid2smiles[96] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(COP(O)(=O)OP(O)(=O)OC2OC(CO)C(O)C(O)C2O)C(O)C1O" # GDP-mannose
        cid2smiles[100] = "CCC(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # propanoyl-CoA
        cid2smiles[101] = "Nc1nc2NCC(CNc3ccc(cc3)C(=O)NC(CCC(O)=O)C(O)=O)Nc2c(=O)[nH]1" # THF
        cid2smiles[104] = "OC1C(COP(O)(=O)OP(O)(O)=O)OC(C1O)n1cnc2c1nc[nH]c2=O" # IDP
        cid2smiles[105] = "OC1C(COP(O)(O)=O)OC(C1O)n1ccc(=O)[nH]c1=O" # UMP
        cid2smiles[106] = "O=c1cc[nH]c(=O)[nH]1" # uracil
        cid2smiles[112] = "Nc1ccn(C2OC(COP(O)(=O)OP(O)(O)=O)C(O)C2O)c(=O)n1" # CMP
        cid2smiles[130] = "OC1C(COP(O)(O)=O)OC(C1O)n1cnc2c1nc[nH]c2=O" # IMP
        cid2smiles[131] = "Nc1ncnc2n(cnc12)C1CC(O)C(COP(O)(=O)OP(O)(=O)OP(O)(O)=O)O1" # dATP
        cid2smiles[143] = "C12CNc3nc(N)[nH]c(=O)c3N1CN(C2)c1ccc(cc1)C(=O)NC(CCC(O)=O)C(O)=O" # 5,10-methylene-THF
        cid2smiles[144] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(COP(O)(O)=O)C(O)C1O" # GMP
        cid2smiles[147] = "Nc1ncnc2[nH]cnc12" # adenine
        cid2smiles[154] = "CCCCCCCCCCCCCCCC(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # palmitoyl-CoA
        cid2smiles[167] = "OC1C(COP(O)(=O)OP(O)(=O)OC2OC(C(O)C(O)C2O)C(O)=O)OC(C1O)n1ccc(=O)[nH]c1=O" # UDP-glucuronate
        cid2smiles[178] = "Cc1c[nH]c(=O)[nH]c1=O" # thymine 
        cid2smiles[190] = "OC1COC(OP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2ccc(=O)[nH]c2=O)C(O)C1O" # UDP-D-xylose
        cid2smiles[206] = "Nc1ncnc2n(cnc12)C1CC(O)C(COP(O)(=O)OP(O)(O)=O)O1" # dADP
        cid2smiles[212] = "Nc1ncnc2n(cnc12)C1OC(CO)C(O)C1O" # adenosine
        cid2smiles[214] = "Cc1cn(C2CC(O)C(CO)O2)c(=O)[nH]c1=O" # thymidine
        cid2smiles[224] = "Nc1ncnc2n(cnc12)C1OC(COP(O)(=O)OS(O)(=O)=O)C(O)C1O" # Adenosine 5'-phosphosulfate
        cid2smiles[234] = "[H]C(=O)N(CC1CNc2nc(N)[nH]c(=O)c2N1)c1ccc(cc1)C(=O)NC(CCC(O)=O)C(O)=O" # 10-Formyl-THF
        cid2smiles[239] = "Nc1ccn(C2CC(O)C(COP(O)(O)=O)O2)c(=O)n1" # dCMP
        cid2smiles[242] = "Nc1nc2[nH]cnc2c(=O)[nH]1" # guanine
        cid2smiles[250] = "[H]C(=O)c1c(CO)cnc(C)c1O" # pyridoxal
        cid2smiles[253] = "OC(=O)c1cccnc1" # nicotinate
        cid2smiles[261] = "[H]C(=O)c1ccccc1" # Benzaldehyde
        cid2smiles[262] = "O=c1[nH]cnc2nc[nH]c12" # hypoxanthine
        cid2smiles[294] = "OCC1OC(C(O)C1O)n1cnc2c(O)ncnc12" # inosine
        cid2smiles[295] = "OC(=O)c1cc(=O)[nH]c(=O)[nH]1" # orotate
        cid2smiles[299] = "OCC1OC(C(O)C1O)n1ccc(=O)[nH]c1=O" # uridine
        cid2smiles[313] = "CC(C)(COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12)C(O)C(=O)NCCC(=O)NCCSC(=O)C(O)=O" # Oxalyl-CoA
        cid2smiles[314] = "Cc1ncc(CO)c(CO)c1O" # pyridoxine
        cid2smiles[332] = "CC(=O)CC(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # acetoacetyl-CoA
        cid2smiles[337] = "OC(=O)C1CC(=O)NC(=O)N1" # dihydroorotate
        cid2smiles[360] = "Nc1ncnc2n(cnc12)C1CC(O)C(COP(O)(O)=O)O1" # dAMP
        cid2smiles[361] = "Nc1nc2n(cnc2c(=O)[nH]1)C1CC(O)C(COP(O)(=O)OP(O)(O)=O)O1" # dGDP
        cid2smiles[362] = "Nc1nc2n(cnc2c(=O)[nH]1)C1CC(O)C(COP(O)(O)=O)O1" # dGMP
        cid2smiles[363] = "Cc1cn(C2CC(O)C(COP(O)(=O)OP(O)(O)=O)O2)c(=O)[nH]c1=O" # dTDP
        cid2smiles[364] = "Cc1cn(C2CC(O)C(COP(O)(O)=O)O2)c(=O)[nH]c1=O" # dTMP
        cid2smiles[385] = "O=c1[nH]c2[nH]cnc2c(=O)[nH]1" # Xanthine
        cid2smiles[387] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(CO)C(O)C1O" # guanosine
        cid2smiles[394] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(COP(O)(=O)OP(O)(=O)OC2OC(CO)C(O)C(O)C2O)C(O)C1O" # GDP-glucose
        cid2smiles[415] = "Nc1nc2NCC(CNc3ccc(cc3)C(=O)NC(CCC(O)=O)C(O)=O)=Nc2c(=O)[nH]1" # Dihydrofolate   
        cid2smiles[423] = "OC(=O)\C=C\c1ccccc1" # trans-cinnamate
        cid2smiles[445] = "C12CNc3nc(N)[nH]c(=O)c3[N+]1=CN(C2)c1ccc(cc1)C(=O)NC(CCC(O)=O)C(O)=O" # 5,10-Methenyl-THF
        cid2smiles[463] = "c1ccc2[nH]ccc2c1" # indole
        cid2smiles[468] = "C12CCC3(C)C(=O)CCC3([H])C1([H])CCc1cc(O)ccc21" # estrone       
        cid2smiles[475] = "Nc1ccn(C2OC(CO)C(O)C2O)c(=O)n1" # Cytidine
        cid2smiles[498] = "Nc1ncnc2n(cnc12)C1OC(COP(O)(=O)OP(O)(=O)OC2OC(CO)C(O)C(O)C2O)C(O)C1O" #  ADP-glucose
        cid2smiles[534] = "Cc1ncc(CO)c(CN)c1O" # Pyridoxamine
        cid2smiles[556] = "OCc1ccccc1" # benzyl alocohol
        cid2smiles[559] = "Nc1ncnc2n(cnc12)C1CC(O)C(CO)O1" # deoxyadenosine
        cid2smiles[575] = "Nc1ncnc2n(cnc12)C1OC2COP(O)(=O)OC2C1O" # cAMP
        cid2smiles[590] = "COc1cc(\C=C\CO)ccc1O" # coniferol
        cid2smiles[617] = "OC1C(COP(O)(=O)OP(O)(=O)OC2OC(C(O)C(O)C2O)C(O)=O)OC(C1O)n1ccc(=O)[nH]c1=O" # UDP-D-galacturonate
        cid2smiles[664] = "[H]C(=N)N1C(CNc2ccc(cc2)C(=O)NC(CCC(O)=O)C(O)=O)CNc2nc(N)[nH]c(=O)c12" # 5-Formimino-THF
        cid2smiles[683] = "CC(C(O)=O)C(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # (S)-Methylmalonyl-CoA
        cid2smiles[705] = "Nc1ccn(C2CC(O)C(COP(O)(=O)OP(O)(=O)O)O2)c(=O)n1" # dCDP
        cid2smiles[785] = "OC(=O)\C=C\c1c[nH]cn1" # Urocanate
        cid2smiles[798] = "[H]C(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # formyl-CoA
        cid2smiles[835] = "CC(O)C(=O)C1=Nc2c(NC1)nc(N)[nH]c2=O" # sepiapterin
        cid2smiles[920] = "CC(C)(COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12)C(O)C(=O)NCCC(=O)NCCSSCC(NC(=O)CCC(N)C(O)=O)C(=O)NCC(O)=O" # CoA-glutathione
        cid2smiles[935] = "OC1COC(OP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2ccc(=O)[nH]c2=O)C(O)C1O" # UDP-L-arabinose
        cid2smiles[951] = "C12CCC3(C)C(O)CCC3([H])C1([H])CCc1cc(O)ccc21" # Estradiol-17beta
        cid2smiles[1100] = "NC(COP(O)(O)=O)Cc1c[nH]cn1" # L-Histidinol phosphate
        cid2smiles[1103] = "OC1C(COP(O)(O)=O)OC(C1O)n1c(cc(=O)[nH]c1=O)C(O)=O" # Orotidine 5'-phosphate
        cid2smiles[1144] = "CC(O)CC(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # (S)-3-Hydroxybutanoyl-CoA;
        cid2smiles[1179] = "OC(=O)C(=O)Cc1ccc(O)cc1" # 4-Hydroxyphenylpyruvate
        cid2smiles[1185] = "OC1C(COP(O)(O)=O)OC(C1O)[n+]1cccc(c1)C(O)=O" # Nicotinate D-ribonucleotide         
        cid2smiles[1213] = "CC(C(O)=O)C(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # (R)-Methylmalonyl-CoA
        cid2smiles[1267] = "OP(O)(=O)OCC(=O)Cc1c[nH]cn1" # Imidazole-acetol phosphate
        cid2smiles[1417] = "OC#N" # cyanate
        cid2smiles[1589] = "c1c[nH]cn1" # imidazole
        cid2smiles[1762] = "OCC1OC(C(O)C1O)n1cnc2c1[nH]c(=O)[nH]c2=O" # Xanthosine
        cid2smiles[2232] = "CC(C)(COP(O)(=O)OP(O)(=O)OC[C@H]1O[C@H]([C@H](O)[C@@H]1OP(O)(O)=O)n1cnc2c(N)ncnc12)[C@@H](O)C(=O)NCCC(=O)NCCSC(=O)CC(=O)CCC(O)=O" # 3-oxoadipyl-CoA
        cid2smiles[2280] = "Nc1nc2n(cnc2c(=O)[nH]1)C1OC(COP(O)(=O)OP(O)(=O)OC2OC(CO)C(O)C(O)C2O)C(O)C1O" # GDP-L-galactose
        cid2smiles[2557] = "CC(C(O)=O)C(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # methylmalonyl-CoA
        cid2smiles[2560] = "CC(=O)n1ccnc1" # N-acetylimidazole
        cid2smiles[2595] = "OC(=O)Cc1cccs1" # Thien-2-ylacetate
        cid2smiles[2666] = "COc1cc(\C=C\C=O)ccc1O" # coniferaldehyde
        cid2smiles[2763] = "OC(=O)C(O)=Cc1ccccc1" # 2-Hydroxy-3-phenylpropenoate
        cid2smiles[2953] = "CC(O)C(O)C1=Nc2c(NC1)[nH]c(N)nc2=O" # 7,8-Dihydrobiopterin
        cid2smiles[3067] = "[H]C(=O)c1cccc(O)c1" # 3-Hydroxybenzaldehyde      
        cid2smiles[3351] = "OCc1cccc(O)c1" # 3-Hydroxybenzyl alcohol
        cid2smiles[3483] = "Nc1ncnc2n(cnc12)C1OC(COP(O)(=O)OP(O)(=O)OP(O)(=O)OP(O)(O)=O)C(O)C1O" # Adenosine tetraphosphate         
        cid2smiles[3493] = "NC(C(O)=O)c1ccc(O)cc1" # D-4-Hydroxyphenylglycine
        cid2smiles[3598] = "CC1OC(OP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2ccc(N)nc2=O)C(O)CC1O" # CDP-3,6-dideoxy-D-glucose
        cid2smiles[3599] = "CC1OC(OP(O)(=O)OP(O)(=O)OCC2OC(C(O)C2O)n2ccc(N)nc2=O)C(O)CC1O" # CDP-3,6-dideoxy-D-mannose
        cid2smiles[3680] = "OC(=O)CCC1NC=NC1=O" # 4-imidazolone-5-propanoate
        cid2smiles[3794] = "OC1C(COP(O)(O)=O)OC(C1O)n1cnc2c(NC(CC(O)=O)C(O)=O)ncnc12" # N6-(1,2-Dicarboxyethyl)-AMP
        cid2smiles[4268] = "CC1OC(OP(O)(=O)OP(O)(=O)OCC2OC(CC2O)n2cc(C)c(=O)[nH]c2=O)C(O)C(O)C1N" # dTDP-4-amino-4,6-dideoxy-D-glucose
        cid2smiles[4677] = "NC(=O)c1ncn(C2OC(COP(O)(O)=O)C(O)C2O)c1N" # 5'-Phospho-ribosyl-5-amino-4-imidazole carboxamide
        cid2smiles[4823] = "Nc1c(ncn1C1OC(COP(O)(O)=O)C(O)C1O)C(=O)NC(CC(O)=O)C(O)=O" # 1-(5'-Phosphoribosyl)-5-amino-4-(N-succinocarboxamide)-imidazole
        cid2smiles[5512] = "OCC1OC(CC1O)n1cnc2c1nc[nH]c2=O" # deoxyinosine 
        cid2smiles[5551] = "C12SC(C)(C)C(N1C(=O)C2NC(=O)Cc1ccccc1)C(O)=O" # Penicillin G
        cid2smiles[5598] = "OC(=O)CNC(=O)Cc1ccccc1" # Phenylacetylglycine
        cid2smiles[5268] = "CCCC(O)CC(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # (S)-Hydroxyhexanoyl-CoA
        cid2smiles[5269] = "CCCC(=O)CC(=O)SCCNC(=O)CCNC(=O)C(O)C(C)(C)COP(O)(=O)OP(O)(=O)OCC1OC(C(O)C1OP(O)(O)=O)n1cnc2c(N)ncnc12" # 3-Oxohexanoyl-CoA
        cid2smiles[6827] = "C12SC(C)(C)C(N1C(=O)C2NC(=O)C(N)c1ccc(O)cc1)C(O)=O" # amoxicillin
        cid2smiles[7086] = "OC(=O)Cc1ccccc1" # Phenylacetic acid;
        cid2smiles[7761] = "C1(NC(=O)Cc2cccs2)C(=O)N2C(C(O)=O)=C(COC(C)=O)CSC12[H]" # cefalotin
        cid2smiles[7756] = "C12SCC(COC(C)=O)=C(N1C(=O)C2N)C(O)=O" # 7-Aminocephalosporanic acid
        cid2smiles[11355] = "NC1C=CC(=CC1OC(=C)C(O)=O)C(O)=O" # 4-Amino-4-deoxychorismate
        cid2smiles[11907] = "Cc1cn(C2CC(C(COP(=O)([O-])OP(=O)([O-])OC3C(C(C(=O)C(C)O3)O)O)O2)O)c(=O)nc1O" # 4,6-Dideoxy-4-oxo-dTDP-D-glucose

        # override ringed sugars
        cid2smiles[117] = "OC1C(COP(O)(=O)O)OC(O)C1O" # D-ribose 5-phosphate
        cid2smiles[119] = "OP(O)(=O)OP(O)(=O)OC1OC(COP(O)(O)=O)C(O)C1O" # PRPP
        cid2smiles[121] = "OCC1OC(O)C(O)C1O" # D-ribose
        cid2smiles[216] = "OCC1OC(O)C(O)C1O" # arabinose
        cid2smiles[252] = "OC2C(O)C(O)C(O)OC2COC(OC1CO)C(O)C(O)C1O" # isomaltose
        cid2smiles[309] = "OC1C(O)COC1(O)CO" # D-ribulose
        cid2smiles[310] = "OC1C(O)COC1(O)CO" # D-xylulose
        cid2smiles[312] = "OC1C(O)COC1(O)CO" # L-xylulose
        cid2smiles[476] = "OC1C(O)C(O)COC1O" # D-lyxose
        cid2smiles[620] = "OCC1OC(OP(O)(=O)O)C(O)C1O" # D-ribose 1-phosphate
        cid2smiles[673] = "OP(=O)(O)OCC1OC(O)CC1O" # 2-deoxy-D-ribose 5-phosphate
        cid2smiles[1112] = "O=P(O)(O)OCC1OC(O)C(O)C1O" # D-arabinose 5-phopsphate

        nist = Nist()
        obs_fname = "../data/thermodynamics/formation_energies.csv"
        thermo = PsuedoisomerTableThermodynamics.FromCsvFile(obs_fname)
        cids_from_kegg = set(nist.GetAllCids() + thermo.get_all_cids()).difference(cid2smiles.keys())

        for cid in cids_from_kegg:
            try:
                cid2smiles[cid] = kegg.cid2mol(cid).ToSmiles()
            except KeggParseException as e:
                logging.warning("Cannot calculate pKas: " + str(e))
                continue

        dissociation_chemaxon = DissociationConstants()
        for cid, smiles in sorted(cid2smiles.iteritems()):
            logging.info("Using ChemAxon to find the pKa values for %s - C%05d" %
                         (kegg.cid2name(cid), cid))
            diss_table = Molecule._GetPseudoisomerMap(smiles, format='smiles',
                mid_pH=default_pH, min_pKa=0, max_pKa=14, T=default_T)
            dissociation_chemaxon.cid2DissociationTable[cid] = diss_table
            html_writer.write('<p><b>C%05d - %s</b></br>\n' % (cid, kegg.cid2name(cid)))
            diss_table.WriteToHTML(html_writer, T=default_T)
            html_writer.write('</p>\n')
        
        dissociation_chemaxon.ToDatabase(db, 'dissociation_constants_chemaxon')

    if False:
        # Print all the values in the dissociation table to the HTML file
        dissociation = DissociationConstants.FromDatabase(db, 
                                            'dissociation_constants_chemaxon')
                
        for cid in sorted(dissociation.GetAllCids()):
            diss_table = dissociation.GetDissociationTable(cid)
            html_writer.write('<h2>%s - C%05d</h2>\n' %
                              (kegg.cid2name(cid), cid))
            if diss_table is not None:
                diss_table.WriteToHTML(html_writer)
                html_writer.write('</br>\n')

    if False:
        cid2mol = {117:None}
        dissociation = DissociationConstants.FromChemAxon(cid2mol, html_writer)
        for cid in cid2mol.keys():
            diss_table = dissociation.GetDissociationTable(cid)
            print "*** C%05d ***" % cid
            print diss_table
            
    if False:
        dissociation1 = DissociationConstants.FromDatabase(db,'dissociation_constants')
        dissociation2 = DissociationConstants.FromDatabase(db,'dissociation_constants_chemaxon')
        reaction = Reaction(names='dihydroorotase', sparse_reaction={1:-1, 337:-1, 438:1})
        
        for cid in reaction.get_cids():
            print "C%05d" % cid
            print dissociation1.GetDissociationTable(cid).GetTransformedDeltaGs(pH=7, I=0, pMg=0, T=298.15)
            print dissociation2.GetDissociationTable(cid).GetTransformedDeltaGs(pH=7, I=0, pMg=0, T=298.15)

        print dissociation1.ReverseTransformReaction(reaction, pH=7, I=0, pMg=0, T=298.15, cid2nH={1:2, 438:6, 337:5})
        print dissociation2.ReverseTransformReaction(reaction, pH=7, I=0, pMg=0, T=298.15, cid2nH={1:2, 438:6, 337:5})
        