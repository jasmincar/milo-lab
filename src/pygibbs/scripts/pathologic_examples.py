import numpy as np
import logging
from pygibbs.kegg_reaction import Reaction
from pygibbs.pathologic import Pathologic
from toolbox.database import SqliteDatabase
from toolbox.html_writer import HtmlWriter
import sys
from pygibbs.thermodynamic_estimators import LoadAllEstimators
from pygibbs.stoichiometric_lp import OptimizationMethods
from pygibbs.thermodynamic_constants import R, default_T
from pygibbs.kegg import Kegg


def ban_toxic_compounds(pl):
    """Bans compounds known to be toxic"""
    pl.ban_compound(546)  # Methylglyoxal
    
def add_carbon_counts(pl):
    """Add reactions counting carbons in various
       plausible fermentation products.
    """
    reactions = [
        #Reaction.FromFormula("C00246 => 4 C06265", name="Butyrate makeup"),
        #Reaction.FromFormula("C02632 => 4 C06265", name="Isobutyrate makeup"),
        Reaction.FromFormula("C00042 => 4 C06265", name="Succinate makeup"),
        #Reaction.FromFormula("C00022 => 3 C06265", name="Pyruvate makeup"),
        #Reaction.FromFormula("C00163 => 3 C06265", name="Propionate makeup"),
        #Reaction.FromFormula("C01013 => 3 C06265", name="3-hydroxypropionate makeup"),
        Reaction.FromFormula("C00186 => 3 C06265", name="Lactate makeup"),
        Reaction.FromFormula("C00033 => 2 C06265", name="Acetate makeup"),
        Reaction.FromFormula("C00469 => 2 C06265", name="Ethanol makeup"),
        Reaction.FromFormula("C00058 => 1 C06265", name="Formate makeup"),
        Reaction.FromFormula("C00011 => 1 C06265", name="CO2 makeup"),
        ]
    for rxn in reactions:
        pl.add_reaction(rxn)
    
    """
    cofactor_reactions = [
        Reaction.FromFormula("C00282 => null", name="Free H2"),
        ]
    for rxn in cofactor_reactions:
        pl.add_cofactor_reaction(rxn)
    """
        
def add_cofactor_reactions(pl):
    pl.add_cofactor_reaction(Reaction.FromFormula("C00001 <=> null", name='Free H2O'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00009 <=> null", name='Free Pi'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00013 <=> null", name='Free PPi'))

def add_XTP_reactions(pl, direction='<=>'):
    """
        Adds phosphorylated nucleotide reactions, which ignore thermodynamics
        and phosphate balance constraints.
        The direction arguments can be set to allow them only in one direction.
        For example, a useful scenario would be to force a pathway
        not to consume ATP without constraining it for zero ATP production.
    """
    pl.add_cofactor_reaction(Reaction.FromFormula("C00002 %s C00008" % direction, name='ATP to ADP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00002 %s C00020" % direction, name='ATP to AMP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00008 %s C00020" % direction, name='ADP to AMP'))

    pl.add_cofactor_reaction(Reaction.FromFormula("C00131 %s C00206" % direction, name='dATP to dADP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00131 %s C00360" % direction, name='dATP to dAMP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00206 %s C00360" % direction, name='dADP to dAMP'))

    pl.add_cofactor_reaction(Reaction.FromFormula("C00081 %s C00104" % direction, name='ITP to IDP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00081 %s C00130" % direction, name='ITP to IMP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00104 %s C00130" % direction, name='IDP to IMP'))

    pl.add_cofactor_reaction(Reaction.FromFormula("C00044 %s C00035" % direction, name='GTP to GDP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00044 %s C00144" % direction, name='GTP to GMP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00035 %s C00144" % direction, name='GDP to GMP'))

    pl.add_cofactor_reaction(Reaction.FromFormula("C00063 %s C00112" % direction, name='CTP to CDP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00063 %s C00055" % direction, name='CTP to CMP'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00112 %s C00055" % direction, name='CDP to CMP'))

def add_redox_reactions(pl, NAD_only=False):
    # all electron transfer reactions
    pl.add_cofactor_reaction(Reaction.FromFormula("C00003 <=> C00004", name='NAD redox'))
    pl.add_cofactor_reaction(Reaction.FromFormula("C00006 <=> C00005", name='NADP redox'))
    if not NAD_only:
        pl.add_cofactor_reaction(Reaction.FromFormula("C00016 <=> C01352", name='FAD redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C00138 <=> C00139", name='ferredoxin redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C00030 <=> C00028", name='acceptor/donor redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C00125 <=> C00126", name='ferricytochrome c redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C00996 <=> C00999", name='ferricytochrome b5 redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C01070 <=> C01071", name='ferricytochrome c-553 redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C05906 <=> C01617", name='leucocyanidin redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C00343 <=> C00342", name='thioredoxin disulfide redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C03648 <=> C00974", name='cis-3,4-Leucopelargonidin redox'))
        pl.add_cofactor_reaction(Reaction.FromFormula("C05684 <=> C01528", name='selenide redox'))
    else:
        pl.ban_compound(16)    # FAD(ox)
        pl.ban_compound(1352)  # FAD(red)
        pl.ban_compound(138)   # ferredoxin (ox)
        pl.ban_compound(139)   # ferredoxin (red)
        pl.ban_compound(30)    # acceptor
        pl.ban_compound(28)    # donor
        pl.ban_compound(125)   # ferricytochrome c (ox)
        pl.ban_compound(126)   # ferricytochrome c (red)
        pl.ban_compound(996)   # ferricytochrome b5 (ox)
        pl.ban_compound(999)   # ferricytochrome b5 (red)
        pl.ban_compound(1070)  # ferricytochrome c-553 (ox)
        pl.ban_compound(1071)  # ferricytochrome c-553 (red)
        pl.ban_compound(5906)  # leucocyanidin (ox)
        pl.ban_compound(1617)  # leucocyanidin (red)
        pl.ban_compound(343)   # thioredoxin disulfide (ox)
        pl.ban_compound(342)   # thioredoxin disulfide (red)
        pl.ban_compound(3648)  # cis-3,4-Leucopelargonidin (ox)
        pl.ban_compound(974)   # cis-3,4-Leucopelargonidin (red)
        pl.ban_compound(5684)  # selenide (ox)
        pl.ban_compound(1528)  # selenide (red)

    
def example_glycolysis(thermo):
    
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=15,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    add_cofactor_reactions(pl, free_ATP_hydrolysis=False)
    ban_toxic_compounds(pl)
    #add_carbon_counts(pl)
    #r = Reaction.FromFormula("C00031 => 6 C06265")
    r = Reaction.FromFormula("C00031 + 3 C00008 => 2 C00186 + 3 C00002")
    #r.Balance()
    pl.find_path("GLC => 2 LAC, 3 ATP, No methylglyoxal", r)

def example_lower_glycolysis(thermo):
    
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=8,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    add_cofactor_reactions(pl)
    add_redox_reactions(pl)
    #r = Reaction.FromFormula("C00003 + C00118 + C00001 => C00022 + C00004 + C00009")
    r = Reaction.FromFormula("C00118 => C00022")
    #r.Balance()
    pl.find_path("GAP => PYR", r)

def example_oxidative(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=10,
                    maximal_dG=0,
                    thermodynamic_method=OptimizationMethods.MAX_TOTAL,
                    update_file=None)
    add_cofactor_reactions(pl)
    add_redox_reactions(pl, NAD_only=False)
    r = Reaction.FromFormula("C00022 => 3 C00011")
    #r.Balance()
    pl.find_path("oxidative", r)

def example_reductive(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=15,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    add_cofactor_reactions(pl)
    add_redox_reactions(pl)
    r = Reaction.FromFormula("3 C00011 => C00022")
    #r.Balance()
    pl.find_path("reductive", r)
    
def example_formate(thermo, product_cid=22, co2_conc=1e-5):
    co2_hydration = Reaction.FromFormula("C00011 + C00001 => C00288")
    co2_hydration_dG0_prime = float(thermo.GetTransfromedKeggReactionEnergies([co2_hydration]))
    carbonate_conc = co2_conc * np.exp(-co2_hydration_dG0_prime / (R*default_T))
    thermo.bounds[11] = (co2_conc, co2_conc)
    thermo.bounds[288] = (carbonate_conc, carbonate_conc)
    
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=20,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    add_cofactor_reactions(pl, free_ATP_hydrolysis=True)
    add_redox_reactions(pl, NAD_only=False)
   
    pl.delete_reaction(134) # formate:NADP+ oxidoreductase
    pl.delete_reaction(519) # Formate:NAD+ oxidoreductase
    pl.delete_reaction(24) # Rubisco
    pl.delete_reaction(581) # L-serine:NAD+ oxidoreductase (deaminating)
    pl.delete_reaction(220) # L-serine ammonia-lyase
    pl.delete_reaction(13) # glyoxylate carboxy-lyase (dimerizing; tartronate-semialdehyde-forming)
    pl.delete_reaction(585) # L-Serine:pyruvate aminotransferase
    pl.delete_reaction(1440) # D-Xylulose-5-phosphate:formaldehyde glycolaldehydetransferase
    pl.delete_reaction(5338) # 3-hexulose-6-phosphate synthase
    
    
    pl.add_reaction(Reaction.FromFormula("C06265 => C00011", name="CO2 uptake"))
    pl.add_reaction(Reaction.FromFormula("C06265 => C00288", name="carbonate uptake"))
    pl.add_reaction(Reaction.FromFormula("C06265 => C00058", name="formate uptake"))

    r = Reaction.FromFormula("5 C06265 + C00058 => C%05d" % product_cid) # at least one formate to product
    #r.Balance()
    
    kegg = Kegg.getInstance()
    pl.find_path("formate to %s" % kegg.cid2name(product_cid), r)

def example_rpi_bypass(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=10,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    add_cofactor_reactions(pl)
    #add_redox_reactions(pl)
    pl.delete_reaction(1056) # ribose-phosphate isomerase
    pl.delete_reaction(1081) # ribose isomerase

    r = Reaction.FromFormula("C00117 => C01182")
    #r.Balance()
    pl.find_path("rpi_bypass", r)

def example_three_acetate(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=20,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    add_cofactor_reactions(pl)
    #add_redox_reactions(pl)
    pl.delete_reaction(761) # F6P + Pi = E4P + acetyl-P
    pl.delete_reaction(1621) # X5P + Pi = GA3P + acetyl-P

    r = Reaction.FromFormula("C00031 => 3 C00033")
    #r.Balance()
    pl.find_path("three_acetate", r)
    
def example_three_pyruvate(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=20,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    #add_cofactor_reactions(pl)
    #add_XTP_reactions(pl, '=>')
    #add_redox_reactions(pl)
    #pl.delete_reaction(761) # F6P + Pi = E4P + acetyl-P
    #pl.delete_reaction(1621) # X5P + Pi = GA3P + acetyl-P

    r = Reaction.FromFormula("C00031 + 3 C00011 + 3 C00004 => 3 C00022 + 3 C00001 + 3 C00003")
    #r.Balance()
    pl.find_path("three_pyr", r)    

def example_more_than_two_pyruvate(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=20,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    #add_cofactor_reactions(pl)
    #add_XTP_reactions(pl, '=>')
    #add_redox_reactions(pl)
    #pl.delete_reaction(761) # F6P + Pi = E4P + acetyl-P
    #pl.delete_reaction(1621) # X5P + Pi = GA3P + acetyl-P

    r = Reaction.FromFormula("3 C00031 + 3 C00011 + C00003 => 7 C00022 + 3 C00001 + C00004")
    r.Balance()
    pl.find_path("more_than_two_pyr", r)

def example_glucose_to_ethanol_and_formate(thermo):
    pl = Pathologic(db=SqliteDatabase('../res/gibbs.sqlite', 'r'),
                    public_db=SqliteDatabase('../data/public_data.sqlite'),
                    html_writer=HtmlWriter('../res/pathologic.html'),
                    thermo=thermo,
                    max_solutions=None,
                    max_reactions=15,
                    maximal_dG=0.0,
                    thermodynamic_method=OptimizationMethods.GLOBAL,
                    update_file=None)
    #add_cofactor_reactions(pl)
    #add_XTP_reactions(pl, '=>')
    #add_redox_reactions(pl)
    #pl.delete_reaction(761) # F6P + Pi = E4P + acetyl-P
    #pl.delete_reaction(1621) # X5P + Pi = GA3P + acetyl-P

    r = Reaction.FromFormula("2 C00031 + 3 C00001 => 6 C00058 + 3 C00469")
    r.Balance()
    pl.find_path("glucose_to_ethanol_and_formate", r)
        
def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    estimators = LoadAllEstimators()
    thermo = estimators['UGC']
    thermo.SetConditions(pH=7.5, I=0.2)
    #example_lower_glycolysis(thermo)
    #example_oxidative(thermo)
    #example_glycolysis(thermo)
    #example_formate(thermo, product=22)
    #example_formate(thermo, product_cid=31)
    #example_rpi_bypass(thermo)
    #example_three_acetate(thermo)
    #example_three_pyruvate(thermo)
    #example_more_than_two_pyruvate(thermo)
    example_glucose_to_ethanol_and_formate(thermo)

if __name__ == '__main__':
    main()

# Handy reference
#
# name          =  CID
# ----------------------
# atp           = C00002
# adp           = C00008
# pi            = C00009
# co2           = C00011
# carbonate     = C00288
# nad           = C00003
# nadh          = C00004
# glucose       = C00031
# g6p           = C00092
# fbp           = C00354
# bpg           = C00236
# g3p           = C00118
# threepg       = C00197
# pep           = C00074
# pyruvate      = C00022
# succinyl_coa  = C00091
# acetyl_coa    = C00024
# lactate       = C00186
# acetate       = C00033
# methylglyoxal = C00546
# ethanol       = C00469
# formate       = C00058
# carbon        = C06265
# electron      = C05359


