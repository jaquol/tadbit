"""
26 Nov 2012


"""

from os.path                           import exists
from pytadbit.boundary_aligner.aligner import align
from pytadbit                          import tadbit
from pytadbit.experiment               import Experiment
from string                            import ascii_lowercase as letters
from warnings                          import warn
from copy                              import deepcopy as copy
from cPickle                           import load, dump
from pytadbit.alignment                import Alignment, randomization_test
from numpy                             import log2
from random                            import random

try:
    from matplotlib import pyplot as plt
except ImportError:
    warn('matplotlib not found\n')




def load_chromosome(in_f, fast=2):
    """
    Load Chromosome from file. Chromosome might have been saved through the
    :func:`Chromosome.save_chromosome`.
    
    :param in_f: path to a saved Chromosome file
    :param 2 fast: if fast=2 do not load Hi-C data (in the case that they were
       saved in a separate file see :func:`Chromosome.save_chromosome`). If fast
       is equal to 1, weight would be skipped from load in order to save memory.
       Finally if fast=0, both weights and Hi-C data will be loaded.
    
    :returns: Chromosome object

    TODO: remove first try/except type error... this is loading old experiments
    """
    dico = load(open(in_f))
    name = ''
    crm = Chromosome(dico['name'])
    for name in dico['experiments']:
        xpr = Experiment(name, dico['experiments'][name]['resolution'], 
                         no_warn=True)
        xpr.tads       = dico['experiments'][name]['tads']
        xpr.wght       = dico['experiments'][name]['wght']
        xpr.hic_data   = dico['experiments'][name]['hi-c']
        xpr.brks       = dico['experiments'][name]['brks']
        xpr.conditions = dico['experiments'][name]['cond']
        xpr.size       = dico['experiments'][name]['size']
        try:
            crm.experiments.append(xpr)
        except TypeError:
            continue
    crm.size            = dico['size']
    crm.r_size          = dico['r_size']
    crm.max_tad_size    = dico['max_tad_size']
    crm.forbidden       = dico['forbidden']
    crm._centromere     = dico['_centromere']
    if type(dico['experiments'][name]['hi-c']) == str and fast!= int(2):
        try:
            dicp = load(open(in_f + '_hic'))
        except IOError:
            raise Exception('ERROR: file {} not found\n'.format(
                dico['experiments'][name]['hi-c']))
        for name in dico['experiments']:
            crm.get_experiment(name).hic_data = dicp[name]['hi-c']
            if fast != 1:
                crm.get_experiment(name).wght = dicp[name]['wght']
    elif not fast:
        warn('WARNING: data not saved correctly for fast loading.\n')
    return crm


class Chromosome(object):
    """
    Chromosome object designed to deal with Topologically Associating Domains
    predictions from different experiments, in different cell types for a given
    chromosome of DNA, and compare them.

    :param name: name of the chromosome (might be a chromosome name for example).
    :param None resolution: resolution of the experiments. All experiments may
       have the same resolution
    :param None experiment_handlers: :py:func:`list` of paths to files
       containing the definition of TADs corresponding to different experiments
       (or output of tadbit)
    :param None experiment_names: :py:func:`list` of names for each experiment
    :param 3000000 max_tad_size: maximum size of TAD allowed. TADs longer than
        this will not be considered, and relative chromosome size will be reduced
        accordingly
    :param 0 chr_len: size of the DNA chromosome in bp. By default it will be
        inferred from the distribution of TADs.

    :return: Chromosome object

    """
    def __init__(self, name, experiment_resolutions=None, tad_handlers=None,
                 experiment_handlers=None, experiment_names=None,
                 max_tad_size=3000000, chr_len=0, parser=None):
        self.name             = name
        self.max_tad_size     = max_tad_size
        self.size             = self._given_size = self.r_size = chr_len
        self.size             = ChromosomeSize(self.size)
        self.r_size           = RelativeChromosomeSize(self.size)
        self.forbidden        = {}
        self.experiments      = ExperimentList([], self)
        self._centromere      = None
        self.alignment        = AlignmentDict()
        if tad_handlers:
            for i, handler in enumerate(tad_handlers or []):
                name = experiment_names[i] if experiment_names else None
                self.add_experiment(name, experiment_resolutions[i],
                                    tad_handler=handler, parser=parser)
        if experiment_handlers:
            for i, handler in enumerate(experiment_handlers or []):
                name = experiment_names[i] if experiment_names else None
                try:
                    xpr = self.get_experiment(name)
                    xpr.load_experiment(handler)
                    continue
                except:
                    pass
                if type(handler) == Experiment:
                    name = name or handler.name
                    self.experiments.append(handler)
                else:
                    self.add_experiment(name, experiment_resolutions[i],
                                        xp_handler=handler, parser=parser)


    def _get_forbidden_region(self, xpr):
        """
        Find regions where there is no info in any of the experiments.
        This is used to calculate relative chromosome size.
        """
        if not xpr.tads:
            return
        forbidden = []
        for pos in xrange(len(xpr.tads)):
            start = float(xpr.tads[pos]['start'])
            end   = float(xpr.tads[pos]['end'])
            diff  = end - start
            if diff * xpr.resolution > self.max_tad_size:
                forbidden += range(int(start), int(end+1))
                xpr.tads[pos]['brk'] = None
        if not self.forbidden:
            self.forbidden = dict([(f, None) for f in forbidden])
        else:
            self.forbidden = dict([(f, None) for f in 
                                   set(forbidden).intersection(self.forbidden)])
        # search for centromere:
        self._search_centromere(xpr)
        # add centromere as forbidden region:
        if self._centromere:
            for pos in xrange(int(self._centromere[0]),
                              int(self._centromere[1])):
                self.forbidden[pos] = 'Centromere'
        self.__update_size(xpr)


    def get_experiment(self, name):
        """
        This can also be done directly through Chromosome.experiments[name].
        
        :param name: name of the wanted experiment
        :returns: :class:`pytadbit.Experiment`
        """
        for exp in self.experiments:
            if exp.name == name:
                return exp
        raise Exception('ERROR: experiment ' +
                        '{} not found\n'.format(name))
                

    def save_chromosome(self, out_f, fast=True, divide=True, force=False):
        """
        Save Chromosome object to file (it uses :py:func:`pickle.load` from the
        :py:mod:`cPickle`). Once saved, the object may be loaded through
        :func:`load_chromosome`.

        :param out_f: path to file to dump the :py:mod:`cPickle` object.
        :param True fast: if True, skips Hi-D data and weights
        :param True divide: if True writes 2 pickles, one with what would result
           by using the fast option, and the second with Hi-C and weights data.
           Second file name will be extended by '_hic' (ie: with
           out_f='chromosome12.pik' we would obtain chromosome12.pik and
           chromosome12.pik_hic). When loaded :func:`load_chromosome` will
           automatically search for both files.
        :param False force: overwrite existing file.

        """
        while exists(out_f) and not force:
            out_f += '_'
        dico = {}
        dico['experiments'] = {}
        if divide:
            dicp = {}
        for xpr in self.experiments:
            dico['experiments'][xpr.name] = {
                'size'      : xpr.size,
                'brks'      : xpr.brks,
                'cond'      : xpr.conditions,
                'tads'      : xpr.tads,
                'resolution': xpr.resolution,
                'hi-c'      : None,
                'wght'      : None}
            if fast:
                continue
            if divide:
                dicp[xpr.name] = {
                    'wght': xpr.wght,
                    'hi-c': xpr.hic_data}
                dico['experiments'][xpr.name]['wght'] = None
                dico['experiments'][xpr.name]['hi-c'] = None
            else:
                dico['experiments'][xpr.name]['wght'] = xpr.wght
                dico['experiments'][xpr.name]['hi-c'] = xpr.hic_data
        dico['name']            = self.name
        dico['size']            = self.size
        dico['r_size']          = self.r_size
        dico['max_tad_size']    = self.max_tad_size
        dico['forbidden']       = self.forbidden
        dico['_centromere']     = self._centromere
        out = open(out_f, 'w')
        dump(dico, out)
        out.close()
        if not fast:
            out = open(out_f + '_hic', 'w')
            dump(dicp, out)
            out.close()


    def align_experiments(self, names=None, verbose=False, randomize=False,
                          rnd_method='interpolate', **kwargs):
        """
        Align prediction of boundaries of two different experiments. Resulting
        alignment will be stored in the self.experiment list.
        
        :param None names: list of names of experiments to align. If None
            align all.
        :param experiment1: name of the first experiment to align
        :param experiment2: name of the second experiment to align
        :param -0.1 penalty: penalty of inserting a gap in the alignment
        :param 100000 max_dist: Maximum distance between 2 boundaries allowing
            match (100Kb seems fair with HUMAN chromosomes)
        :param False verbose: print somethings
        :param False randomize: check alignment quality by comparing
            randomization of boundaries over Chromosomes of same size. This will
            return a extra value, the p-value of accepting that observed
            alignment is not better than random alignment
        :param interpolate rnd_method: by default uses interpolation of TAD
           distribution. Alternative is 'shuffle' where TADs are simply shuffled
        :param reciprocal method: if global, Needleman-Wunsch is used to align
            (see :func:`pytadbit.boundary_aligner.globally.needleman_wunsch`);
            if reciprocal, a method based on reciprocal closest boundaries is
            used (see :func:`pytadbit.boundary_aligner.reciprocally.reciprocal`)

        :returns: the alignment and the score of the alignment (by default)
        """
        if names:
            xpers = ExperimentList([self.get_experiment(n) for n in names],
                                   self)
        else:
            xpers = self.experiments
        tads = []
        for xpr in xpers:
            if not xpr.tads:
                raise Exception('No TADs defined, use find_tad function.\n')
            tads.append([x * xpr.resolution for x in xpr.brks])
        # new
        aligneds, score = align(tads, verbose=verbose, **kwargs)
        name = tuple(sorted([x.name for x in xpers]))
        ali = Alignment(name, aligneds, xpers, score=score)
        self.alignment[name] = ali
        if verbose:
            print self.alignment[name]
        # old
        # self.alignment[name] = {}
        # for xpr, ali in zip(xpers, aligneds):
        #     self.alignment[name][xpr.name] = ali
        # if verbose:
        #     self.print_alignment(xpers=xpers)
        if not randomize:
            # return self.get_alignment(name), score
            return ali
        p_value = randomization_test(xpers, score=score, method=rnd_method,
                                     verbose=verbose, r_size=self.r_size,
                                     **kwargs)
        return score, p_value


    def add_experiment(self, name, resolution=None, tad_handler=None,
                       xp_handler=None, replace=False, parser=None,
                       conditions=None, **kwargs):
        """
        Add Hi-C experiment to Chromosome
        
        :param name: name of the experiment or Experiment object
        :param resolution: resolution of the experiment (need if name is not an
           Experiment)
        :param handler: path to tsv file
        :param False replace: overwrite experiments loaded under the same name
        :param None parser: a parser function that returns a tuple of lists
           representing the data matrix, and the length of a row/column, with
           this file example.tsv:

           ::
           
             chrT_001	chrT_002	chrT_003	chrT_004
             chrT_001	629	164	88	105
             chrT_002	164	612	175	110
             chrT_003	88	175	437	100
             chrT_004	105	110	100	278
           
           the output of parser('example.tsv') might be:
           ``[([629, 164, 88, 105, 164, 612, 175, 110, 88, 175, 437, 100, 105,
           110, 100, 278]), 4]``
        
        """
        if not name:
            name = ''.join([letters[int(random() * len(letters))] \
                            for _ in xrange(5)])
            warn('No name provided, random name generated: {}\n'.format(name))
        if name in self.experiments:
            if 'hi-c' in self.get_experiment(name) and not replace:
                warn('''Hi-C data already loaded under the name: {}.
                This experiment will be kept under {}.\n'''.format(name,
                                                                   name + '_'))
                name += '_'
        if type(name) == Experiment:
            self.experiments.append(name)
        elif resolution:
            self.experiments.append(Experiment(name, resolution, xp_handler,
                                               tad_handler, parser=parser,
                                               conditions=conditions, **kwargs))
        else:
            raise Exception('resolution param is needed\n')


    def find_tad(self, experiments, name=None, n_cpus=None, verbose=True,
                 max_tad_size="auto", no_heuristic=False, batch_mode=False):
        """
        Call :func:`pytadbit.tadbit.tadbit` function to calculate the position
        of Topologically associated domains
        
        :param experiment: A square matrix of interaction counts in hi-C
            data or a list of such matrices for replicated experiments. The
            counts must be evenly sampled and not normalized. 'experiment'
            might be either a list of list, a path to a file or a file handler
        :param None n_cpus: The number of CPUs to allocate to tadbit. The
            value default is the total number of CPUs minus 1.
        :param auto max_tad_size: an integer defining maximum size of TAD.
            Default (auto) defines it to the number of rows/columns.
        :param False no_heuristic: whether to use or not some heuristics
        :param False batch_mode: if True, all experiments will be concatenated
            into one for the search of TADs. The resulting TADs found are stored
            under the name 'batch' plus a concatenation of the experiment names
            passed (i.e.: if experiments=['exp1', 'exp2'], the name would be:
            'batch_exp1_exp2').

        TODO: check option -> name for batch mode... some dirty changes....
        
        """
        if batch_mode:
            matrix = []
            if not name:
                name = 'batch'
            experiments = experiments or self.experiments
            xprs = []
            for xpr in experiments:
                if not type(xpr) == Experiment:
                    xprs.append(self.get_experiment(xpr))
                else:
                    xprs.append(xpr)
            resolution = xprs[0].resolution
            for xpr in sorted(xprs, key=lambda x: x.name):
                if xpr.resolution != resolution:
                    raise Exception('All Experiments might have the same ' +
                                    'resolution\n')
                matrix.append(xpr.hic_data[0])
                if name.startswith('batch'):
                    name += '_' + xpr.name
            result, weights = tadbit(matrix,
                                     n_cpus=n_cpus, verbose=verbose,
                                     max_tad_size=max_tad_size,
                                     no_heuristic=no_heuristic,
                                     get_weights=True)
            experiment = Experiment(name, resolution, xp_handler=matrix,
                                    tad_handler=result, weights=weights)
            self.add_experiment(experiment)
            return
        if type(experiments) is not list:
            experiments = [experiments]
        for experiment in experiments:
            if not type(experiment) == Experiment:
                xpr = self.get_experiment(experiment)
            result, weights = tadbit(xpr.hic_data,
                                     n_cpus=n_cpus, verbose=verbose,
                                     max_tad_size=max_tad_size,
                                     no_heuristic=no_heuristic,
                                     get_weights=True)
            xpr.load_tad_def(result, weights=weights)
            self._get_forbidden_region(xpr)


    def __update_size(self, xpr):
        """
        Update chromosome size and relative size after loading new Hi-C
        experiments.

        Unless Chromosome size was defined by hand.
        
        """
        if not self._given_size:
            self.size = max(xpr.tads[max(xpr.tads)]['end'] * xpr.resolution,
                            self.size)
            self.size   = ChromosomeSize(self.size)
        self.r_size = self.size - len(self.forbidden) * xpr.resolution
        self.r_size = RelativeChromosomeSize(self.size)


    def visualize(self, name, tad=None, paint_tads=False, axe=None, show=True,
                  logarithm=True):
        """
        Visualize the matrix of Hi-C interactions

        :param name: name of the experiment to visualize
        :param None tad: a given TAD in the form:
           ::
           
             {'start': start,
              'end'  : end,
              'brk'  : end,
              'score': score}
        :param False paint_tads: draw a box around TADs defined for this
           experiment
        :param None axe: an axe object from matplotlib can be passed in order to
           customize the picture.
        :param True show: either to pop-up matplotlib image or not
        :param True logarithm: show logarithm

        TODO: plot normalized data
        """
        xper = self.get_experiment(name)
        if logarithm:
            fun = log2
        else:
            fun = lambda x: x
        vmin = fun(min(xper.hic_data[0]) or (1 if logarithm else 0))
        vmax = fun(max(xper.hic_data[0]))
        size = xper.size
        if not axe:
            axe = plt.subplot(111)
        if tad:
            matrix = [[xper.hic_data[0][i+size*j] \
                       for i in xrange(int(tad['start']), int(tad['end']))] \
                      for j in xrange(int(tad['start']), int(tad['end']))]
        else:
            matrix = [[xper.hic_data[0][i+size*j]\
                       for i in xrange(size)] \
                      for j in xrange(size)]
        img = axe.imshow(fun(matrix), origin='lower', vmin=vmin, vmax=vmax,
                         interpolation="nearest")
        if not paint_tads:            
            if show:
                plt.show()
            return img
        for i, tad in xper.tads.iteritems():
            axe.hlines(tad['start'], tad['start'], tad['end'], colors='k')
            axe.hlines(tad['end'], tad['start'], tad['end'], colors='k')
            axe.vlines(tad['start'], tad['start'], tad['end'], colors='k')
            axe.vlines(tad['end'], tad['start'], tad['end'], colors='k')
            axe.text(tad['start'] + abs(tad['start']-tad['end'])/2 - 1,
                    tad['start'] + abs(tad['start']-tad['end'])/2 - 1, str(i))
            if not tad['brk']:
                for j in xrange(int(tad['start']), int(tad['end']), 4):
                    axe.hlines(j, tad['start'], tad['end'], colors='k')
        if show:
            plt.show()


    def get_tad_hic(self, tad, x_name, normed=True, matrix_num=0):
        """
        Retrieve the Hi-C data matrix corresponding to ma given TAD.
        
        :param tad: a given TAD -> :py:class:`dict`
        :param x_name: name of the experiment
        :param True normed: if Hi-C data has to be normalized
        
        :returns: Hi-C data matrix for the given TAD
        """
        beg, end = int(tad['start']), int(tad['end'])
        xpr = self.get_experiment(x_name)
        size = xpr.size
        matrix = [[0 for _ in xrange(beg, end)]\
                  for _ in xrange(beg, end)]
        for i, tadi in enumerate(xrange(beg, end)):
            tadi = tadi * size
            for j, tadj in enumerate(xrange(beg, end)):
                matrix[j][i] = xpr.hic_data[matrix_num][tadi + tadj]
                if not normed:
                    continue
                try:
                    matrix[j][i] = float(matrix[j][i]) \
                                   / xpr.wght[0][tadi + tadj]
                except ZeroDivisionError:
                    matrix[j][i] = 0.0
        return matrix


    def iter_tads(self, x_name, normed=True):
        """
        Iterate over TADs corresponding to a given experiment.
        
        :param x_name: name of the experiment
        :param True normed: normalize Hi-C data returned
        
        :yields: Hi-C data corresponding to each TAD
        """
        if not self.get_experiment(x_name).hic_data:
            raise Exception('No Hi-c data for {} experiment\n'.format(x_name))
        for name, ref in self.get_experiment(x_name).tads.iteritems():
            if not ref['brk']:
                continue
            yield name, self.get_tad_hic(ref, x_name, normed=normed)


    def set_max_tad_size(self, value):
        """
        Change maximum size allowed for TADs. Also apply it to computed
        experiments.

        :param 3000000 value: an int
        """
        self.max_tad_size = value
        for xpr in self.experiments:
            for tad in xpr.tads:
                if (xpr.tads[tad]['end'] - xpr.tads[tad]['start']) \
                   * xpr.resolution < self.max_tad_size:
                    xpr.tads[tad]['brk'] = xpr.tads[tad]['end']
                else:
                    xpr.tads[tad]['brk'] = None
            xpr.brks = []
            for tad in xpr.tads:
                if xpr.tads[tad]['brk']:
                    xpr.brks.append(xpr.tads[tad]['brk'])
            

    def _search_centromere(self, xpr):
        """
        Search for centromere in chromosome, assuming that
        :class:`Chromosome` corresponds to a real chromosome.
        Add a boundary to all experiments where the centromere is.
         * A centromere is defined as the largest area where the rows/columns of
           the Hi-C matrix are empty.
        """
        beg = end = 0
        size = xpr.size
        try:
            hic = xpr.hic_data[0]
        except TypeError:
            return
        # search for largest empty region of the chromosome
        best = (0, 0, 0)
        pos = 0
        for pos, raw in enumerate(xrange(0, size * size, size)):
            if sum(hic[raw:raw + size]) == 0 and not beg:
                beg = float(pos)
            if sum(hic[raw:raw + size]) != 0 and beg:
                end = float(pos)
                if (end - beg) > best[0]:
                    best = ((end - beg), beg, end)
                beg = end = 0
        # this is for weared cases where centromere is at the end of Hi-C data
        if beg and not end:
            end = float(pos)
            if (end - beg) > best[0]:
                best = ((end - beg), beg, end)
        beg, end = best[1:]
        if not beg or not end:
            return
        tads = xpr.tads
        # if we already have a centromere defined, check if it can be reduced
        if self._centromere:
            if beg > self._centromere[0]:
                # readjust TADs that have been split around the centromere
                for tad in tads:
                    if tads[tad]['end'] == self._centromere[0]:
                        tads[tad]['end'] = beg
                self._centromere[0] = beg
            if end < self._centromere[1]:
                # readjust TADs that have been split around the centromere
                for tad in tads:
                    if tads[tad]['start'] == self._centromere[1]:
                        tads[tad]['start'] = end
                self._centromere[1] = end
        else:
            self._centromere = [beg, end]
        # split TADs overlapping  with the centromere
        if [True for t in tads.values() \
            if t['start'] < beg < t['end'] \
            and t['start'] < end < t['end']]:
            tad  = len(tads)
            plus = 0
            while tad + plus >= 1:
                start = tads[tad - 1 + plus]['start']
                final = tads[tad - 1 + plus]['end']
                # centromere found?
                if start < beg < final and start < end < final:
                    tads[tad]     = copy(tads[tad - 1])
                    tads[tad]['start'] = end
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        tads[tad]['brk'] = None
                    else:
                        tads[tad]['brk'] = tads[tad]['end']
                    tad -= 1
                    tads[tad] = copy(tads[tad])
                    tads[tad]['end'] = beg
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        tads[tad]['brk'] = None
                    else:
                        tads[tad]['brk'] = tads[tad]['end']
                    plus = 1
                else:
                    tads[tad] = copy(tads[tad - 1 + plus])
                tad -= 1
            xpr.brks = []
            for tad in tads:
                if tads[tad]['brk']:
                    xpr.brks.append(tads[tad]['brk'])


class ExperimentList(list):
    """
    :py:func:`list` of :class:`pytadbit.Experiment`
    
    Modified getitem, setitem, and append in order to be able to search
    experiments by index or by name.

    ExperimentList are linked to the Chromosome

    linked to a :class:`pytadbit.Chromosome`
    """
    def __init__(self, thing, crm):
        super(ExperimentList, self).__init__(thing)
        self.crm = crm
        

    def __getitem__(self, i):
        try:
            return super(ExperimentList, self).__getitem__(i)
        except TypeError:
            for nam in self:
                if nam.name == i:
                    return nam
            raise KeyError('Experiment {} not found\n'.format(i))


    def __setitem__(self, i, exp):
        try:
            super(ExperimentList, self).__setitem__(i, exp)
            exp.crm = self.crm
            self.crm._get_forbidden_region(exp)
        except TypeError:
            for j, nam in enumerate(self):
                if nam.name == i:
                    exp.crm = self.crm
                    self[j] = exp
                    self.crm._get_forbidden_region(exp)
                    break
            else:
                exp.crm = self.crm
                self.append(exp)
                self.crm._get_forbidden_region(exp)


    def __delitem__(self, i):
        try:
            super(ExperimentList, self).__delitem__(i)
        except TypeError:
            for j, nam in enumerate(self):
                if nam.name == i:
                    exp = self.pop(j)
                    del(exp)
                    break
            else:
                raise KeyError('Experiment {} not found\n'.format(i))


    def append(self, exp):
        if exp.name in [e.name for e in self]:
            self[exp.name] = exp
            self.crm._get_forbidden_region(exp)
        else:
            super(ExperimentList, self).append(exp)
            self.crm._get_forbidden_region(exp)
            exp.crm = self.crm


class AlignmentDict(dict):
    """
    :py:func:`dict` of :class:`pytadbit.Alignment`
    
    Modified getitem, setitem, and append in order to be able to search
    alignments by index or by name.

    linked to a :class:`pytadbit.Chromosome`
    """

    def __getitem__(self, nam):
        try:
            return super(AlignmentDict, self).__getitem__(nam)
        except KeyError:
            for i, key in enumerate(self):
                if nam == i:
                    return self[key]
            raise TypeError('Alignment {} not found\n'.format(i))


class ChromosomeSize(int):
    """
    This is an integer.
    
    Chromosome size in base pairs
    """
    def __init__(self, thing):
        super(ChromosomeSize, self).__init__(thing)


class RelativeChromosomeSize(int):
    """
    This is an integer.
    
    Relative Chromosome size in base pairs. Equal to Chromosome size minus
    forbidden regions (eg: the centromere)
    """
    def __init__(self, thing):
        super(RelativeChromosomeSize, self).__init__(thing)
