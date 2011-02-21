import logging
import matplotlib
matplotlib.use('Agg')
import pylab
import PIL, PIL.Image, StringIO

from django.http import Http404
from django.http import HttpResponse
from gibbs import reaction
from gibbs import reaction_graph_form


def ReactionGraph(request):    
    """Renders a page for a particular reaction."""
    form = reaction_graph_form.ReactionGraphForm(request.GET)
    if not form.is_valid():
        logging.error(form.errors)
        raise Http404

    rxn = reaction.Reaction.FromForm(form)
    
    pylab.rc('font', size=9)
    figure = pylab.figure()
    pylab.title(rxn.GetQueryString(), figure=figure)
    xvals = None
    dgs = None
    if form.cleaned_vary_pmg:
        xvals = pylab.arange(0.001, 14.0, 0.1)
        dgs = [rxn.DeltaGTag(pMg=x) for x in xvals]
        pylab.xlabel('pMg', figure=figure)
        pylab.figtext(0.7, 0,
                      '* pH=%.2f, Ionic Strength=%.2f' % (rxn.ph, rxn.i_s),
                      figure=figure)                    
    elif form.cleaned_vary_is:
        xvals = pylab.arange(0.001, 0.35, 0.01)
        dgs = [rxn.DeltaGTag(ionic_strength=x) for x in xvals]
        pylab.xlabel('Ionic Strength', figure=figure)
        pylab.figtext(0.7, 0,
                      '* pH=%.2f, pMg=%.2f' % (rxn.ph, rxn.pmg),
                      figure=figure) 
    else:
        xvals = pylab.arange(0.001, 14.0, 0.1)
        dgs = [rxn.DeltaGTag(pH=x) for x in xvals]
        pylab.xlabel('pH', figure=figure)
        pylab.figtext(0.7, 0,
                      '* pMg=%.2f, Ionic Strength=%.2f' % (rxn.pmg, rxn.i_s),
                      figure=figure) 
        
    pylab.ylabel('dG\'', figure=figure)
    pylab.plot(xvals, dgs, '-', figure=figure)

    response = HttpResponse(mimetype="image/png")
    pylab.savefig(response, format="png", figure=figure)
    return response