import sys
from pygibbs.groups import GroupContribution, GroupMissingTrainDataError
from pygibbs.group_decomposition import GroupDecompositionError
import pybel
from pygibbs.kegg import KeggParseException
from toolbox import database
from toolbox.html_writer import HtmlWriter
from pygibbs.groups_data import GroupsData
from pygibbs.group_decomposition import GroupDecomposer
from pygibbs.kegg import Kegg
from pygibbs.thermodynamic_analysis import ThermodynamicAnalysis

def main():
    db = database.SqliteDatabase('../res/gibbs.sqlite')
    html_writer = HtmlWriter('../res/dG0_test.html')
    G = GroupContribution(db, html_writer=html_writer)
    G.init()

    groups_data = GroupsData.FromGroupsFile("../data/thermodynamics/groups_species.csv")
    group_decomposer = GroupDecomposer(groups_data)
    (pH, pMg, I, T) = (7, 3, 0.1, 298.15)
    
    cids = [8586]
    #smiles = ['C[NH2+]CC(=O)O']
    smiles = []
    smarts = pybel.Smarts("[N;H2;X4;R0;+1]")
    
    mols = []
    for cid in cids:
        try:
            mols += [G.kegg().cid2mol(cid)]
        except KeggParseException:
            continue
        except KeyError:
            continue
    
    for s in smiles:
        mols += [pybel.readstring('smiles', s)]
    
    for m in mols:
        print '-' * 50
        try:
            m.removeh()
            print "SMARTS PATTERN FOUND AT:", smarts.findall(m)
            decomposition = group_decomposer.Decompose(m, ignore_protonations=False, strict=True)
            print decomposition.ToTableString()
            pmap_m = G.estimate_pmap(m)
            print pmap_m
            print "dG0'_f (M): ", pmap_m.Transform(pH, pMg, I, T)
        except KeyError as e:
            sys.stderr.write(str(e) + "\n")
        except GroupMissingTrainDataError as e:
            sys.stderr.write(str(e) + "\n")
            for miss_list in e.missing_groups:
                sys.stderr.write(str(miss_list) + "\n")
        except GroupDecompositionError as e:
            sys.stderr.write(str(e) + "\n")
            pass
        m.draw()
    
    print '-' * 50
    if True: # calculate the pKa for some common groups
        print "-NH2 -> -NH3[+] : pKa = %.1f" % G.GetpKa_group("-N", 3, 1)
        print "-COO[-] -> -COOH : pKa = %.1f" % G.GetpKa_group("-COO", 1, 0)
        print "-OPO3[2-] -> -HOPO3[-] : pKa = %.1f" % G.GetpKa_group("-OPO3", 1, -1)
        print "-CO-OPO3[2-] -> -CO-HOPO3[-] : pKa = %.1f" % G.GetpKa_group("CO-OPO3", 1, -1, 0)
        print "-CO-HOPO3[-] -> -CO-H2OPO3 : pKa = %.1f" % G.GetpKa_group("CO-OPO3", 2, 0, 0)
        print "-OPO3.Mg -> -OPO3[2-] : pK_Mg = %.1f" % G.GetpK_Mg_group("-OPO3", 0, 0, 1)

if __name__ == "__main__":
    main()
    