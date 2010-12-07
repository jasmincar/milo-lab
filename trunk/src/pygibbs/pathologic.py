import sys, pylab, cplex
from pygibbs.stoichiometric_lp import Stoichiometric_LP
from pygibbs.kegg import KeggPathologic
from pygibbs.groups import GroupContribution
from pygibbs.feasibility import thermodynamic_pathway_analysis
from toolbox.html_writer import HtmlWriter
from toolbox.util import _mkdir

################################################################################
#                               CONSTANTS & DEFAULTS                           #
################################################################################
class Pathologic:
    def __init__(self):
        cplex.Cplex() # causes CPLEX to print its initialization message
        _mkdir('../res/pathologic')
        self.LOG_FILE = open('../res/pathologic/pathologic.log', 'w')
        self.gc = GroupContribution(sqlite_name="gibbs.sqlite", html_name="pathologic", log_file=self.LOG_FILE)
        self.gc.init()
        self.thermodynamic_method = "global" # options are: "none", "pCr", "MCMF", "global" or "localized"
        self.maximal_dG = 0 # use this to change the thermodynamic constraints to have a different MCMF (when set to 0, it is the usual feasibility measure)
        self.max_reactions = None
        self.max_solutions = 100
        self.flux_relaxtion_factor = None
        self.kegg_patholotic = KeggPathologic(self.LOG_FILE, self.gc.kegg())
        self.update_file = '../data/thermodynamics/database_updates.txt'

    def __del__(self):
        self.LOG_FILE.close()
        
    def find_path(self, experiment_name, source=None, target=None):
        _mkdir('../res/pathologic/' + experiment_name)
        self.gc.HTML.write('<a href="pathologic/' + experiment_name + '.html">' + experiment_name + '</a><br>\n')
        exp_html = HtmlWriter('../res/pathologic/' + experiment_name + '.html')
        exp_html.write("<p><h1>%s</h1>\n" % experiment_name)

        exp_html.write('<input type="button" class="button" onclick="return toggleMe(\'__parameters__\')" value="Show Parameters">\n')
        exp_html.write('<div id="__parameters__" style="display:none">')

        exp_html.write('<h2>Conditions:</h2> pH = %g, I = %g, T = %g<br>\n' % (self.gc.pH, self.gc.I, self.gc.T))
        exp_html.write('<h2>Thermodynamic constraints:</h2> ')
        if (self.thermodynamic_method == "none"):
            exp_html.write("ignore thermodynamics")
        elif (self.thermodynamic_method == "pCr"):
            exp_html.write("Concentration Range Requirement Analysis, Cmid = %g M" % self.gc.c_mid)
        elif (self.thermodynamic_method == "MCMF"):
            exp_html.write("Maximal Chemical Motive Force Analysis, %g M < C < %g M" % self.gc.c_range)
        elif (self.thermodynamic_method == "global"):
            exp_html.write("Global constraints, %g M < C < %g M, dG < %.1f" % (self.gc.c_range[0], self.gc.c_range[1], self.maximal_dG))
        elif (self.thermodynamic_method == "localized"):
            exp_html.write("Localized bottlenecks, %g M < C < %g M" % self.gc.c_range)
        else:
            raise Exception("thermodynamic_method must be: 'none', 'pCr', 'MCMF', 'global' or 'localized'")
        exp_html.write('<br>\n')
        
        if (source != None):
            exp_html.write('<h2>Source Reaction:</h2>\n')
            exp_html.write_ul(['%d x %s(C%05d)' % (coeff, self.kegg_patholotic.cid2compound[cid].name, cid) for (cid, coeff) in source.iteritems()])
        if (target != None):
            exp_html.write('<h2>Target (biomass) Reaction:</h2>\n')
            exp_html.write_ul(['%d x %s(C%05d)' % (coeff, self.kegg_patholotic.cid2compound[cid].name, cid) for (cid, coeff) in target.iteritems()])

        self.kegg_patholotic.update_database(self.update_file, exp_html)
        (f, S, compounds, reactions) = self.kegg_patholotic.get_unique_cids_and_reactions()
        exp_html.write('<h2>%d reactions with %d unique compounds</h2>\n' % (len(reactions), len(compounds)))
        
        exp_html.write('</div><br>\n')
        
        self.LOG_FILE.write("All compounds:\n")
        for c in range(len(compounds)):
            self.LOG_FILE.write("%05d) C%05d = %s\n" % (c, compounds[c].cid, compounds[c].name))
        self.LOG_FILE.write("All reactions:\n")
        for r in range(len(reactions)):
            self.LOG_FILE.write("%05d) R%05d = %s\n" % (r, reactions[r].rid, str(reactions[r])))

        # Find a solution with a minimal total flux
        sys.stderr.write("Preparing the CPLEX object for solving the minimal flux problem ... ")
        exp_html.write('<b>Minimum flux</b>')
        slip = Stoichiometric_LP("Pathologic", self.LOG_FILE)
        slip.add_stoichiometric_constraints(f, S, compounds, reactions, source, target)
        slip.set_objective()
        slip.export("../res/pathologic/%s/%03d_lp.txt" % (experiment_name, 0))
        exp_html.write(' (<a href="%s/%03d_lp.txt">LP file</a>): ' % (experiment_name, 0))
        sys.stderr.write("[DONE]\n")
        sys.stderr.write("Solving ... ")
        if (not slip.solve() ):
            exp_html.write("<b>There are no solutions!</b>")
            sys.stderr.write("There are no solutions. Quitting!")
            return
        sys.stderr.write("writing solution ...")
        best_flux = slip.get_total_flux()
        self.write_current_solution(exp_html, slip, experiment_name)
        sys.stderr.write("[DONE]\n")

        sys.stderr.write("Preparing the CPLEX object for solving the minimal reaction problem using MILP ... ")
        milp = Stoichiometric_LP("Pathologic", self.LOG_FILE)
        milp.solution_index = 1
        milp.add_stoichiometric_constraints(f, S, compounds, reactions, source, target)
        milp.add_milp_variables()
        if (self.flux_relaxtion_factor != None):
            milp.add_flux_constraint(best_flux * self.flux_relaxtion_factor)
        if (self.max_reactions != None):
            milp.add_reaction_num_constraint(self.max_reactions)
        
        if (self.thermodynamic_method == "pCr"):
            milp.add_dGr_constraints(self.gc, pCr=True, MCMF=False, maximal_dG=0)
        elif (self.thermodynamic_method == "MCMF"):
            milp.add_dGr_constraints(self.gc, pCr=False, MCMF=True, maximal_dG=0)
        elif (self.thermodynamic_method == "global"):
            milp.add_dGr_constraints(self.gc, pCr=False, MCMF=False, maximal_dG=self.maximal_dG)
        elif (self.thermodynamic_method == "localized"):
            milp.add_localized_dGf_constraints(self.gc)
        
        sys.stderr.write("[DONE]\n")

        for index in range(1, self.max_solutions+1):
            # create the MILP problem to constrain the previous solutions not to reappear again.
            sys.stderr.write("Round %03d, solving using MILP ... " % (milp.solution_index))
            milp.set_objective()
            milp.export("../res/pathologic/%s/%03d_lp.txt" % (experiment_name, milp.solution_index))
            exp_html.write('<b>Solution #%d</b> (<a href="%s/%03d_lp.txt">LP file</a>): '  % (index, experiment_name, index))
            if (not milp.solve()):
                exp_html.write("<b>No solution found</b>")
                sys.stderr.write("No more solutions. Quitting!")
                return
            sys.stderr.write("writing solution ...")
            self.write_current_solution(exp_html, milp, experiment_name)
            milp.ban_current_solution()
            sys.stderr.write("[DONE]\n")
        exp_html.close()

    def write_current_solution(self, exp_html, lp, experiment_name):
        (sol_reactions, sol_fluxes) = lp.get_fluxes()
        solution_id = '%03d' % lp.solution_index
        
        exp_html.write('%d reactions, flux = %g, \n' % (len(sol_reactions), lp.get_total_flux()))

        # draw network as a graph and link to it
        Gdot = self.kegg_patholotic.draw_pathway(sol_reactions, sol_fluxes)
        Gdot.write('../res/pathologic/%s/%s_graph.svg' % (experiment_name, solution_id), prog='dot', format='svg')
        #exp_html.embed_dot(Gdot)
        exp_html.write(' <a href="%s/%s_graph.svg" target="_blank">network</a>' % (experiment_name, solution_id))

        exp_html.write('<input type="button" class="button" onclick="return toggleMe(\'%s\')" value="Show">\n' % (solution_id))
        exp_html.write('<div id="%s" style="display:none">' % solution_id)

        # write the solution for the concentrations in a table
        if (lp.use_dG_f):
            (cids, concentrations) = lp.get_conc()
            exp_html.write('<p>Compound Concentrations<br>\n')
            exp_html.write('<table border="1">\n')
            exp_html.write('  ' + '<td>%s</td>'*2 % ("KEGG CID", "Concentration [M]") + '\n')
            for c in xrange(len(cids)):
                exp_html.write('<tr><td>C%05d</td><td>%.2g</td></tr>\n' % (cids[c], concentrations[c]))
            exp_html.write('</table></br>\n')
        
        # perform feasibility analysis and write the results
        res = self.margin_analysis(exp_html, sol_reactions, sol_fluxes, experiment_name, solution_id)
        self.write_kegg_pathway(exp_html, sol_reactions, sol_fluxes)
        exp_html.write('</div>\n')
        
        for optimization in res.keys():
            score = res[optimization][2]
            exp_html.write(", %s = %g" % (optimization, score))
        exp_html.write('<br>\n')

    def show_Gdot(self, Gdot):
        import gtk
        from toolbox import xdot
    
        win = xdot.DotWindow()
        win.connect('destroy', gtk.main_quit)
        win.set_filter('dot')
        fname = '.dot'
        Gdot.write(fname, format='dot')
        win.open_file(fname)
        gtk.main()
    
    def write_kegg_pathway(self, exp_html, reactions, fluxes):

        def write_reaction(prefix, reaction, flux=1):
            if (flux == 1):
                exp_html.write('%sR%05d&nbsp;&nbsp;%s<br>\n' % (prefix, reaction.rid, str(reaction)))
            else:
                exp_html.write('%sR%05d&nbsp;&nbsp;%s (x%g)<br>\n' % (prefix, reaction.rid, str(reaction), flux))
        
        exp_html.write('<p style="font-family: courier; font-size:10pt">')
        exp_html.write('ENTRY' + '&nbsp;'*7 + 'M-PATHOLOGIC<br>\n')
        exp_html.write('SKIP' + '&nbsp;'*8 + 'FALSE<br>\n')
        exp_html.write('NAME' + '&nbsp;'*8 + 'M-PATHOLOGIC<br>\n')
        exp_html.write('TYPE' + '&nbsp;'*8 + 'MARGIN<br>\n')
        exp_html.write('CONDITIONS' + '&nbsp;'*2 + 'pH=%g,I=%g,T=%g<br>\n' % (self.gc.pH, self.gc.I, self.gc.T))
        exp_html.write('C_MID' + '&nbsp;'*7 + '0.0001<br>\n')
        for r in range(len(reactions)):
            if (r == 0):
                write_reaction('REACTION' + '&nbsp;'*4, reactions[r], fluxes[r])
            else:
                write_reaction('&nbsp;'*12, reactions[r], fluxes[r])
        exp_html.write('///<br></p>\n')

    def margin_analysis(self, exp_html, reactions, fluxes, experiment_name, solution_id):
        cids = [] # I am not using a set() since I want to keep the order of compounds the same as they appear in the reaction
        for r in reactions:
            for cid in r.sparse.keys():
                if (cid not in cids):
                    cids.append(cid)
                
        # convert the list of reactions to a stoichiometric matrix - S
        Nr = len(reactions)
        Nc = len(cids)
        S = pylab.zeros((Nr, Nc))
        rids = []
        for r in range(Nr):
            rids.append(reactions[r].rid)
            for (cid, coeff) in reactions[r].sparse.iteritems():
                c = cids.index(cid)
                S[r, c] = coeff

        return thermodynamic_pathway_analysis(S, rids, fluxes, cids, self.gc, self.gc.kegg(), exp_html)

################################################################################
#                               MAIN                                           #
################################################################################

def main():
    pl = Pathologic()
    
    pl.update_file = '../data/thermodynamics/database_updates_with_MOG_reactions.txt'
    pl.thermodynamic_method = 'global'
    pl.gc.c_range = (1e-6, 1e-2)
    pl.max_solutions = 1
    pl.maximal_dG = -5
    #pl.max_reactions = 10
    
    #source = {}; target = {48:1}
    #name = "=> glyoxylate (%g - %g, MTDF = %.1f)" % (pl.gc.c_range[0], pl.gc.c_range[1], pl.maximal_dG)

    source = {}; target = {197:1}
    name = "=> 3PG (%g - %g, MTDF = %.1f)" % (pl.gc.c_range[0], pl.gc.c_range[1], pl.maximal_dG)
    
    #source = {}; target = {24:1}
    #name = "=> acetyl-CoA (%g - %g, MTDF = %.1f)" % (pl.gc.c_range[0], pl.gc.c_range[1], pl.maximal_dG)
    
    pl.find_path(name, source, target)
    
    #pl.find_path('Glucose to Butanol (Global)', source={31:1}, target={6142:1}, thermo_method="global")

    # TODO: write a clustering algorithm to understand the solutions
   
    # TODO: When using "margin" optimization, there is no constraint on the total flux, and that can cause unwanted results (such as futile cycles).
    # one solution for this is to find all futile cycles and remove them in post-processing.
    # Note that these futile cycles are not of the thermodynamically infeasible kind (i.e. they are futile only if you ignore the co-factors).
    
####################################################################################################

if (__name__ == '__main__'):
    main()
