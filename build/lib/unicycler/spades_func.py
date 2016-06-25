'''
Functions relating to SPAdes assembly steps.

Author: Ryan Wick
email: rrwick@gmail.com
'''

import os
import subprocess
import gzip
import shutil
from .misc import print_section_header, round_to_nearest_odd, get_compression_type, int_to_str, \
                  quit_with_error
from .assembly_graph import AssemblyGraph


def get_best_spades_graph(short1, short2, outdir, read_depth_filter, verbosity, spades_path,
                          threads, keep_temp, kmer_count, min_kmer_frac, max_kmer_frac):
    '''
    This function tries a SPAdes assembly at different kmers and returns the best.
    'The best' is defined as the smallest dead-end count after low-depth filtering.  If multiple
    graphs have the same dead-end count (e.g. zero!) then the highest kmer is used.
    '''
    spades_dir = os.path.join(outdir, 'spades_assembly_temp')
    reads = spades_read_correction(short1, short2, spades_dir, verbosity, threads, spades_path)
    kmer_range = get_kmer_range(short1, short2, spades_dir, verbosity, kmer_count, min_kmer_frac,
                                max_kmer_frac)
    assem_dir = os.path.join(spades_dir, 'assembly')
    print_section_header('Conducting SPAdes assemblies', verbosity)

    # Check to see if the SPAdes assembies already exist. If so, we'll use them instead of doing
    # the assembly again.
    files_exist = True
    file_list = []
    for kmer in kmer_range:
        graph_file = os.path.join(spades_dir, 'k' + str(kmer) + '_assembly_graph.gfa')
        file_list.append(graph_file)
        if not os.path.isfile(graph_file):
            files_exist = False
    if files_exist and verbosity > 0:
        print('Assemblies already exist. Will use these graphs instead of running SPAdes '
              'assemblies:')
        print(''.join(['  ' + x + '\n' for x in file_list]))

    # Conduct a SPAdes assembly for each k-mer (or load existing ones) and score them to choose
    # the best.
    if verbosity == 1:
        print('  k-mer   segments   dead ends         score')
    best_score = 0.0
    best_assembly_graph = None
    for i, kmer in enumerate(kmer_range):
        clean_graph_filename = os.path.join(spades_dir, 'k' + str(kmer) +
                                            '_assembly_graph.gfa')
        if files_exist:
            assembly_graph = AssemblyGraph(clean_graph_filename, kmer)
        else:
            graph_file, paths_file = spades_assembly(reads, assem_dir, kmer_range[:i+1],
                                                     verbosity, threads, spades_path)
            assembly_graph = AssemblyGraph(graph_file, kmer, paths_file=paths_file)
            assembly_graph.clean(read_depth_filter)
            assembly_graph.save_to_gfa(os.path.join(spades_dir, clean_graph_filename), 0)

        dead_ends = assembly_graph.total_dead_end_count()
        segment_count = len(assembly_graph.segments)

        if segment_count == 0:
            score = 0.0
        else:
            score = 1.0 / (segment_count * ((dead_ends + 1) ** 2))
        if verbosity == 1:
            print(int_to_str(kmer).rjust(7) + int_to_str(segment_count).rjust(11) +
                  int_to_str(dead_ends).rjust(12) + '{:.2e}'.format(score).rjust(14))
        if verbosity > 1:
            title = 'SPAdes k=' + int_to_str(kmer) + ' assembly graph summary'
            print(assembly_graph.get_summary(title, file=clean_graph_filename, score=score))
        if score >= best_score:
            best_kmer = kmer
            best_score = score
            best_assembly_graph = assembly_graph

    # Clean up SPAdes files.
    if keep_temp < 2 and os.path.isdir(assem_dir):
        shutil.rmtree(assem_dir)
    if keep_temp < 1 and os.path.isdir(spades_dir):
        shutil.rmtree(spades_dir)

    if verbosity == 1:
        print()

    if best_score == 0.0:
        quit_with_error('none of the SPAdes assemblies produced assembled sequence')

    if verbosity > 0:
        print('Best kmer: ' + str(best_kmer))
        print()

    return best_assembly_graph

def spades_read_correction(short1, short2, spades_dir, verbosity, threads, spades_path):
    '''
    This runs SPAdes with the --only-error-correction option.
    '''
    print_section_header('SPAdes read error correction', verbosity)

    # If the corrected reads already exist, then we just use them and proceed.
    corrected_1 = os.path.join(spades_dir, 'corrected_1.fastq.gz')
    corrected_2 = os.path.join(spades_dir, 'corrected_2.fastq.gz')
    corrected_u = os.path.join(spades_dir, 'corrected_u.fastq.gz')
    if os.path.isfile(corrected_1) and os.path.isfile(corrected_2):
        if verbosity > 0:
            print('Corrected reads already exist. Will use these reads instead of running SPAdes '
                  'error correction:')
            print('  ' + corrected_1)
            print('  ' + corrected_2)
            if os.path.isfile(corrected_u):
                print('  ' + corrected_u)
        return (corrected_1, corrected_2, corrected_u)

    # If the corrected reads don't exist, then we run SPAdes in error correction only mode.
    read_correction_dir = os.path.join(spades_dir, 'read_correction')
    command = [spades_path, '-1', short1, '-2', short2, '-o', read_correction_dir,
               '--threads', str(threads), '--only-error-correction']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    while process.poll() is None:
        spades_output = process.stdout.readline().rstrip().decode('utf-8')
        print_line = verbosity > 1
        if verbosity > 0 and 'Command line:' in spades_output:
            spades_output = ' '.join(spades_output.split())
            print_line = True
        if spades_output and print_line:
            print(spades_output, flush=True)

    spades_error = process.stderr.readline().strip().decode('utf-8')
    if spades_error:
        quit_with_error('SPAdes encountered an error: ' + spades_error)
    if not spades_output:
        quit_with_error('SPAdes failed to complete read correction - please check SPAdes '
                        'installation')

    # Read error correction should be done now, so copy the correct read files to a more permanent
    # location.
    short1_no_extension = strip_read_extensions(short1)
    short2_no_extension = strip_read_extensions(short2)
    corrected_dir = os.path.join(read_correction_dir, 'corrected')
    files = os.listdir(corrected_dir)
    for spades_file in files:
        file_path = os.path.join(corrected_dir, spades_file)
        if short1_no_extension in spades_file:
            shutil.move(file_path, corrected_1)
        elif short2_no_extension in spades_file:
            shutil.move(file_path, corrected_2)
        elif '_unpaired' in spades_file:
            shutil.move(file_path, corrected_u)
    shutil.rmtree(read_correction_dir)

    if not os.path.isfile(corrected_1) or not os.path.isfile(corrected_2):
        quit_with_error('SPAdes read error correction failed')

    if verbosity > 0:
        print()
        print('Corrected reads:')
        print('  ' + corrected_1)
        print('  ' + corrected_2)
        if os.path.isfile(corrected_u):
            print('  ' + corrected_u)

    return (corrected_1, corrected_2, corrected_u)

def spades_assembly(read_files, outdir, kmers, verbosity, threads, spades_path):
    '''
    This runs a SPAdes assembly, possibly continuing from a previous assembly.
    '''
    short1 = read_files[0]
    short2 = read_files[1]
    unpaired = read_files[2]
    kmer_string = ','.join([str(x) for x in kmers])
    this_kmer = 'k' + str(kmers[-1])
    command = [spades_path, '-o', outdir, '-k', kmer_string, '--threads', str(threads)]
    if len(kmers) > 1:
        last_kmer = 'k' + str(kmers[-2])
        command += ['--restart-from', last_kmer]
    else:
        command += ['--only-assembler', '-1', short1, '-2', short2]
        if unpaired:
            command += ['-s', unpaired]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    while process.poll() is None:
        spades_output = process.stdout.readline().rstrip().decode('utf-8')
        if spades_output and verbosity > 1:
            if 'Command line:' in spades_output:
                spades_output = ' '.join(spades_output.split())
            print(spades_output, flush=True)

    spades_error = process.stderr.readline().strip().decode('utf-8')
    if spades_error:
        quit_with_error('SPAdes encountered an error: ' + spades_error)

    graph_file = os.path.join(outdir, 'assembly_graph.fastg')
    paths_file = os.path.join(outdir, 'contigs.paths')

    parent_dir = os.path.dirname(outdir)
    moved_graph_file = os.path.join(parent_dir, this_kmer + '_assembly_graph.fastg')
    shutil.move(graph_file, moved_graph_file)
    moved_paths_file = os.path.join(parent_dir, this_kmer + '_contigs.paths')
    shutil.move(paths_file, moved_paths_file)

    return moved_graph_file, moved_paths_file


def get_kmer_range(reads_1_filename, reads_2_filename, spades_dir, verbosity, kmer_count,
                   min_kmer_frac, max_kmer_frac):
    '''
    Uses the read lengths to determine the k-mer range to be used in the SPAdes assembly.
    '''
    print_section_header('Choosing k-mer range for assembly', verbosity)

    # If the k-mer range file already exists, we use its values and proceed.
    kmer_range_filename = os.path.join(spades_dir, 'kmer_range')
    if os.path.isfile(kmer_range_filename):
        with open(kmer_range_filename, 'r') as kmer_range_file:
            kmer_range = kmer_range_file.readline().strip().split(', ')
        if len(kmer_range) == kmer_count:
            try:
                kmer_range = [int(x) for x in kmer_range]
                if verbosity > 0:
                    print('K-mer range already exists:')
                    print('  ' + kmer_range_filename)
                    print('Will use this existing range:')
                    print('  ' + ', '.join([str(x) for x in kmer_range]))
                return kmer_range
            except ValueError:
                pass

    # If the code got here, then the k-mer range doesn't already exist and we'll create one by
    # examining the read lengths.
    read_lengths = get_read_lengths(reads_1_filename) + get_read_lengths(reads_2_filename)
    read_lengths = sorted(read_lengths)
    median_read_length = read_lengths[len(read_lengths) // 2]
    starting_kmer = round_to_nearest_odd(min_kmer_frac * median_read_length)
    max_kmer = round_to_nearest_odd(max_kmer_frac * median_read_length)
    if starting_kmer < 11:
        starting_kmer = 11
    if max_kmer > 127:
        max_kmer = 127
    interval = 2
    while True:
        kmer_range = list(range(starting_kmer, max_kmer, interval)) + [max_kmer]
        if len(kmer_range) <= kmer_count:
            break
        interval += 2
    kmer_range_str = ', '.join([str(x) for x in kmer_range])

    if verbosity > 0:
        print('Median read length: ' + str(median_read_length))
        print('Starting k-mer:     ' + str(starting_kmer))
        print('Maximum k-mer:      ' + str(max_kmer))
        print('k-mer range:        ' + kmer_range_str)

    kmer_range_file = open(kmer_range_filename, 'w')
    kmer_range_file.write(kmer_range_str)
    kmer_range_file.close()
    return kmer_range

def strip_read_extensions(read_file_name):
    '''
    This function removes certain file extensions from a file name.
    '''
    no_extensions = read_file_name
    lower = no_extensions.lower()
    if lower.endswith('.gz'):
        no_extensions = no_extensions[:-3]
    lower = no_extensions.lower()
    if lower.endswith('.fasta') or lower.endswith('.fastq') or lower.endswith('.fastg'):
        no_extensions = no_extensions[:-6]
    return no_extensions

def get_read_lengths(reads_filename):
    '''
    Returns a list of the read lengths for the given read file.
    '''
    if get_compression_type(reads_filename) == 'gz':
        open_func = gzip.open
    else: # plain text
        open_func = open
    reads = open_func(reads_filename, 'rb')
    read_lengths = []
    i = 0
    for line in reads:
        if i % 4 == 1:
            read_lengths.append(len(line.strip()))
        i += 1
    reads.close()
    return read_lengths