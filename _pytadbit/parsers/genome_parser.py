"""
17 nov. 2014

convert a bunch of fasta files, or a single multi fasta file, into a dictionary
"""

from collections import OrderedDict
from pytadbit.utils.file_handling import magic_open


def parse_fasta(f_names, chr_names=None, verbose=True):
    """
    Parse a list of fasta files, or just one fasta.

    WARNING: The order is important

    :param f_names: list of pathes to files, or just a single path
    :param None chr_names: pass list of chromosome names, or just one. If None
       are passed, then chromosome names will be inferred from fasta headers

    :returns: a sorted dictionary with chromosome names as keys, and sequences
       as values (sequence in upper case)
    """
    if isinstance(f_names, str):
        f_names = [f_names]
    if isinstance(chr_names, str):
        chr_names = [chr_names]

    genome_seq = OrderedDict()
    if len(f_names) == 1:
        header = None
        seq = []
        for line in magic_open(f_names[0]):
            if line.startswith('>'):
                if header:
                    genome_seq[header] = ''.join(seq).upper()
                if not chr_names:
                    header = line[1:].split()[0]
                    if verbose:
                        print 'Parsing %s' % (header)
                else:
                    header = chr_names.pop(0)
                    if verbose:
                        print 'Parsing %s as %s' % (line[1:].rstrip(), header)
                seq = []
                continue
            seq.append(line.rstrip())
        genome_seq[header] = ''.join(seq).upper()
    else:
        for fnam in f_names:
            fhandler = magic_open(fnam)
            try:
                while True:
                    if not chr_names:
                        header = fhandler.next()
                        if header.startswith('>'):
                            header = header[1:].split()[0]
                            genome_seq[header] = ''
                            break
                    else:
                        _ = fhandler.next()
                        header = chr_names.pop(0)
                        genome_seq[header] = ''
                        break
            except StopIteration:
                raise Exception('No crocodiles found, is it fasta?')
            genome_seq[header] = ''.join([l.rstrip() for l in fhandler]).upper()
    return genome_seq
