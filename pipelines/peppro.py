#!/usr/bin/env python
"""
PEPPRO - Run-on sequencing pipeline
"""

__author__ = ["Jason Smith", "Nathan Sheffield", "Mike Guertin"]
__email__ = "jasonsmith@virginia.edu"
__version__ = "0.7.4"


from argparse import ArgumentParser
import os
import sys
import re
import tempfile
import tarfile
import pypiper
from pypiper import build_command
from refgenconf import RefGenConf as RGC, select_genome_config

TOOLS_FOLDER = "tools"
RUNON_SOURCE = ["pro", "gro"]
ADAPTER_REMOVAL = ["fastp", "cutadapt"]
DEDUPLICATORS = ["seqkit", "fqdedup"]
TRIMMERS = ["seqtk", "fastx"]
BT2_IDX_KEY = "bowtie2"


def parse_arguments():
    """
    Parse command-line arguments passed to the pipeline.
    """
    # Argument Parsing from yaml file
    ###########################################################################
    parser = ArgumentParser(description='PEPPRO version ' + __version__)
    parser = pypiper.add_pypiper_args(parser, groups=
        ['pypiper', 'looper', 'ngs'],
        required=["input", "genome", "sample-name", "output-parent"])

    # Pipeline-specific arguments
    parser.add_argument("--runon", dest="runon",
                        default="pro", choices=RUNON_SOURCE,
                        help="Run on sequencing type.")

    parser.add_argument("--adapter", dest="adapter",
                        default="fastp", choices=ADAPTER_REMOVAL,
                        help="Name of adapter removal program")

    parser.add_argument("--dedup", dest="dedup",
                        default="seqkit", choices=DEDUPLICATORS,
                        help="Name of program that removes duplicate reads")

    parser.add_argument("--trimmer", dest="trimmer",
                        default="seqtk", choices=TRIMMERS,
                        help="Name of read trimming program")

    parser.add_argument("--umi", action='store_true', default=False,
                        dest="umi",
                        help="Remove umi with fastp")

    parser.add_argument("--umi_len", dest="umi_len",
                        default="8",
                        help="Specify the length of the UMI."
                             "If your data does not utilize UMIs, set to 0.")

    parser.add_argument("--max_len", dest="max_len",
                        default="30",
                        help="Trim reads to maximum length."
                             " Set to -1 to disable length trimming.")

    parser.add_argument("--sob", action='store_true',
                        dest="sob", default=False,
                        help="Use seqOutBias to produce signal tracks and "
                             "incorporate mappability information.")

    parser.add_argument("--scale", action='store_true',
                        dest="scale", default=False,
                        help="Scale output with seqOutBias when producing"
                             " signal tracks.")

    parser.add_argument("--parts", dest="parts",
                        default="4",
                        help="Split suffix tree generation into <n> parts. "
                             "Increase this value to lower memory use.")

    parser.add_argument("--prealignments", default=[], type=str, nargs="+",
                        help="Space-delimited list of reference genomes to "
                             "align to before primary alignment.")

    parser.add_argument("--TSS-name", default=None,
                        dest="TSS_name", type=str,
                        help="file_name of TSS annotation file.")

    parser.add_argument("--PI-name", default=None,
                        dest="PI_name", type=str,
                        help="file_name of pause index annotation file.")

    parser.add_argument("--pre-name", default=None,
                        dest="pre_name", type=str,
                        help="file_name of pre-mRNA annotation file.")

    parser.add_argument("--anno-name", default=None,
                        dest="anno_name", type=str,
                        help="file_name of genomic annotation file.")

    parser.add_argument("--exon-name", default=None,
                        dest="exon_name", type=str,
                        help="file_name of exon annotation file.")

    parser.add_argument("--intron-name", default=None,
                        dest="intron_name", type=str,
                        help="file_name of intron annotation file.")

    parser.add_argument("--coverage", action='store_true', default=False,
                        dest="coverage",
                        help="Report library complexity using coverage: "
                             "reads / (bases in genome / read length)")

    parser.add_argument("--keep", action='store_true', default=False,
                        dest="keep",
                        help="Keep prealignment BAM files")

    parser.add_argument("--noFIFO", action='store_true', default=False,
                        dest="no_fifo",
                        help="Do NOT use named pipes during prealignments.")

    parser.add_argument("--complexity", action='store_false', default=True,
                        dest="complexity",
                        help="Disable library complexity calculation (faster).")

    parser.add_argument("-V", "--version", action="version",
                        version="%(prog)s {v}".format(v=__version__))

    args = parser.parse_args()

    if not args.input:
        parser.print_help()
        raise SystemExit

    return args


def _align_with_bt2(args, tools, paired, useFIFO, unmap_fq1, unmap_fq2,
                    assembly_identifier, assembly_bt2, outfolder,
                    aligndir=None, bt2_opts_txt=None, dups=False):
    """
    A helper function to run alignments in series, so you can run one alignment
    followed by another; this is useful for successive decoy alignments.

    :param argparse.Namespace args: binding between option name and argument,
        e.g. from parsing command-line options
    :param looper.models.AttributeDict tools: binding between tool name and
        value, e.g. for tools/resources used by the pipeline
    :param bool paired: if True, use paired-end alignment
    :param bool useFIFO: if True, use named pipe instead of file creation
    :param str unmap_fq1: path to unmapped read1 FASTQ file
    :param str unmap_fq2: path to unmapped read2 FASTQ file
    :param str assembly_identifier: text identifying a genome assembly for the
        pipeline
    :param str assembly_bt2: assembly-specific bowtie2 folder (index, etc.)
    :param str outfolder: path to output directory for the pipeline
    :param str aligndir: name of folder for temporary output
    :param str bt2_opts_txt: command-line text for bowtie2 options
    :param bool dups: if True, produce alternative named output
    :return (str, str): pair (R1, R2) of paths to FASTQ files
    """
    if os.path.exists(os.path.dirname(assembly_bt2)):
        pm.timestamp("### Map to " + assembly_identifier)
        if not aligndir:
            align_subdir = "aligned_{}_{}".format(args.genome_assembly,
                                                  assembly_identifier)
            sub_outdir = os.path.join(outfolder, align_subdir)
        else:
            sub_outdir = os.path.join(outfolder, aligndir)

        ngstk.make_dir(sub_outdir)
        if dups:
            bamname = "{}_{}_dups.bam".format(args.sample_name, assembly_identifier)
            summary_name = "{}_{}_bt_aln_dups_summary.log".format(args.sample_name,
                                                                  assembly_identifier)
        else:
            bamname = "{}_{}.bam".format(args.sample_name, assembly_identifier)
            summary_name = "{}_{}_bt_aln_summary.log".format(args.sample_name,
                                                             assembly_identifier)
        mapped_bam = os.path.join(sub_outdir, bamname)
        summary_file = os.path.join(sub_outdir, summary_name)

        out_fastq_pre = os.path.join(
            sub_outdir, args.sample_name + "_" + assembly_identifier)
        if dups:
            out_fastq_r1 = out_fastq_pre + '_unmap_dups_R1.fq'
            out_fastq_r2 = out_fastq_pre + '_unmap_dups_R2.fq'
        else:
            out_fastq_r1 = out_fastq_pre + '_unmap_R1.fq'
            out_fastq_r2 = out_fastq_pre + '_unmap_R2.fq'

        out_fastq_r1_gz = out_fastq_r1  + '.gz'
        out_fastq_r2_gz = out_fastq_r2  + '.gz'

        if useFIFO and paired and not args.keep:
            if dups:
                out_fastq_tmp = os.path.join(sub_outdir,
                                             assembly_identifier + "_dups_bt2")
            else:
                out_fastq_tmp = os.path.join(sub_outdir,
                                             assembly_identifier + "_bt2")
            cmd = "mkfifo " + out_fastq_tmp

            if os.path.exists(out_fastq_tmp):
                os.remove(out_fastq_tmp)
            pm.run(cmd, out_fastq_tmp)

        else:
            if dups:
                out_fastq_tmp = out_fastq_pre + '_unmap_dups.fq'
            else:
                out_fastq_tmp = out_fastq_pre + '_unmap.fq'

        out_fastq_tmp_gz = out_fastq_tmp + ".gz"

        filter_pair = build_command([tools.perl,
            tool_path("filter_paired_fq.pl"), out_fastq_tmp,
            unmap_fq1, unmap_fq2, out_fastq_r1, out_fastq_r2])
        # TODO: make filter_paired_fq work with SE data
        # cmd = build_command([tools.perl,
           # tool_path("filter_paired_fq.pl"), out_fastq_tmp,
           # unmap_fq1, out_fastq_r1])
        # For now, revert to old method

        if not bt2_opts_txt:
            # Default options
            bt2_opts_txt = "-k 1"  # Return only 1 alignment
            bt2_opts_txt += " -D 20 -R 3 -N 1 -L 20 -i S,1,0.50"

        # samtools sort needs a temporary directory
        tempdir = tempfile.mkdtemp(dir=sub_outdir)
        pm.clean_add(tempdir)

        # Build bowtie2 command
        cmd = "(" + tools.bowtie2 + " -p " + str(pm.cores)
        cmd += " " + bt2_opts_txt
        cmd += " -x " + assembly_bt2
        cmd += " --rg-id " + args.sample_name
        cmd += " -U " + unmap_fq1
        cmd += " --un " + out_fastq_tmp
        if args.keep: #  or not paired
            #cmd += " --un-gz " + out_fastq_bt2 # TODO drop this for paired... because repairing with filter_paired_fq.pl
            # In this samtools sort command we print to stdout and then use > to
            # redirect instead of  `+ " -o " + mapped_bam` because then samtools
            # uses a random temp file, so it won't choke if the job gets
            # interrupted and restarted at this step.
            cmd += " | " + tools.samtools + " view -bS - -@ 1"  # convert to bam
            cmd += " | " + tools.samtools + " sort - -@ 1"  # sort output
            cmd += " -T " + tempdir
            cmd += " -o " + mapped_bam
        else:
            cmd += " > /dev/null"
        cmd += ") 2>" + summary_file

        if paired:
            if args.keep or not useFIFO:
                pm.run([cmd, filter_pair], mapped_bam)
            else:
                pm.wait = False
                pm.run(filter_pair, [summary_file, out_fastq_r2_gz],
                       container=pm.container)
                pm.wait = True
                pm.run(cmd, [summary_file, out_fastq_r2_gz],
                       container=pm.container)
        else:
            if args.keep:
                pm.run(cmd, mapped_bam)
            else:
                # TODO: switch to this once filter_paired_fq works with SE
                #pm.run(cmd2, summary_file)
                #pm.run(cmd1, out_fastq_r1)
                pm.run(cmd, out_fastq_tmp_gz)

        pm.clean_add(out_fastq_tmp)

        if not dups:
            cmd = ("grep 'aligned exactly 1 time' " + summary_file +
                   " | awk '{print $1}'")
            align_exact = pm.checkprint(cmd)
            if align_exact:
                ar = float(align_exact)*2
            else:
                ar = 0

            # report aligned reads
            pm.report_result("Aligned_reads_" + assembly_identifier, ar)
            try:
                # wrapped in try block in case Trimmed_reads is not reported 
                # in this pipeline.
                tr = float(pm.get_stat("Trimmed_reads"))
            except:
                print("Trimmed reads is not reported.")
            else:
                res_key = "Alignment_rate_" + assembly_identifier
                pm.report_result(res_key, round(float(ar) * 100 / float(tr), 2))
        
        if paired:
            unmap_fq1 = out_fastq_r1
            unmap_fq2 = out_fastq_r2
        else:
            # Use alternate once filter_paired_fq is working with SE
            #unmap_fq1 = out_fastq_r1
            unmap_fq1 = out_fastq_tmp
            unmap_fq2 = ""

        return unmap_fq1, unmap_fq2
    else:
        msg = "No {} index found in {}; skipping.".format(
            assembly_identifier, os.path.dirname(assembly_bt2))
        print(msg)
        return unmap_fq1, unmap_fq2


def _check_bowtie2_index(rgc, genome_assembly):
    """
    Confirm bowtie2 index is present.

    Checks by simple file count whether the bowtie2 index for a genome
    assembly (as produced by the RefGenie reference builder) contains the
    correct number of non-empty files.

    :param str genomes_folder: refgenconf.RefGenConf rgc: a genome configuration
        instance, which provides relevant genome asset pointers
    :param str genome_assembly: name of the specific assembly of interest,
        e.g. 'mm10'
    """
    try:
        bt2_path = rgc.get_asset(
            genome_assembly, BT2_IDX_KEY, check_exist=os.path.isdir)
    except IOError as e:
        pm.fail_pipeline(e)
    
    if not os.listdir(bt2_path):
        err_msg = "'{}' does not contain any files.\n{}\n{}"
        loc_msg = ("Try updating/confirming the 'genomes' variable in "
                   "'pipelines/pepatac.yaml'.")
        typ_msg = ("Confirm that '{}' "
                   "is the correct genome, and that you have successfully "
                   "built a refgenie genome "
                   "by that name.".format(genome_assembly))
        pm.fail_pipeline(IOError(err_msg.format(bt2_path, loc_msg, typ_msg)))
    else:
        path, dirs, files = next(os.walk(bt2_path))

    # check for bowtie small index
    if [bt for bt in files if bt.endswith('bt2')]:
        bt = ['.1.bt2', '.2.bt2', '.3.bt2', '.4.bt2',
              '.rev.1.bt2', '.rev.2.bt2']
    # check for bowtie large index
    elif [bt for bt in files if bt.endswith('bt2l')]:
        bt = ['.1.bt2l', '.2.bt2l', '.3.bt2l', '.4.bt2l',
              '.rev.1.bt2l', '.rev.2.bt2l']
    # if neither file type present, fail
    else:
        err_msg = "{} does not contain any bowtie2 index files."
        pm.fail_pipeline(IOError(err_msg.format(bt2_path)))

    bt_expected = [genome_assembly + s for s in bt]
    bt_present  = [bt for bt in files if any(s in bt for s in bt_expected)]
    bt_missing  = list(set(bt_expected) - set(bt_present))
    # if there are any missing files (bowtie2 file naming is constant), fail
    if bt_missing:
        err_msg = "The {} bowtie2 index is missing the following file(s): {}"
        pm.fail_pipeline(IOError(
            err_msg.format(genome_assembly,
                           ', '.join(str(s) for s in bt_missing))))
    else:
        for f in bt_present:
            # If any bowtie2 files are empty, fail
            if os.stat(os.path.join(bt2_path, f)).st_size == 0:
                err_msg = "The bowtie2 index file, {}, is empty."
                pm.fail_pipeline(IOError(err_msg.format(f)))

    genome_file = genome_assembly + ".fa"
    fa_files = [fa for fa in files if genome_file in fa]
    if not fa_files:
        # The fasta file does not exist
        err_msg = "Could not find {}.fa in {}."
        pm.fail_pipeline(IOError(
            err_msg.format(genome_assembly, bt2_path)))
    for f in fa_files:
        if os.stat(os.path.join(bt2_path, f)).st_size == 0:
            pm.fail_pipeline(IOError("{} is an empty file.".format(f)))


def tool_path(tool_name):
    """
    Return the path to a tool used by this pipeline.

    :param str tool_name: name of the tool (e.g., a script file_name)
    :return str: real, absolute path to tool (expansion and symlink resolution)
    """

    return os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        TOOLS_FOLDER, tool_name)


def guess_encoding(fq):
    """
    Adapted from Brent Pedersen's "guess_encoding.py"
    https://github.com/brentp/bio-playground/blob/master/reads-utils/guess-encoding.py
    
    Copyright (c) 2018 Brent Pedersen

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

    Guess the encoding of a stream of qual lines.
    """
    RANGES = {
        'Sanger': (33, 73),
        'Illumina-1.8': (33, 74),
        'Solexa': (59, 104),
        'Illumina-1.3': (64, 104),
        'Illumina-1.5': (67, 105)
    }
    
    def get_qual_range(qual_str):
        vals = [ord(c) for c in qual_str]
        return min(vals), max(vals)
    
    def get_encodings_in_range(rmin, rmax, ranges=RANGES):
        valid_encodings = []
        for encoding, (emin, emax) in ranges.items():
            if rmin >= emin and rmax <= emax:
                valid_encodings.append(encoding)
        return valid_encodings

    gmin = 99
    gmax = 0
    valid = []

    err_exit = False

    with open(fq) as fastq_file:
        for line_num, line in enumerate(fastq_file):
            # Python starts at 0; need to start at 1 for this step
            if (line_num + 1) % 4 == 0:
                lmin, lmax = get_qual_range(line.rstrip())

                if lmin < gmin or lmax > gmax:
                    gmin, gmax = min(lmin, gmin), max(lmax, gmax)
                    valid = get_encodings_in_range(gmin, gmax)

                    if len(valid) == 0:
                        print("no encodings for range: "
                              "{}".format((gmin, gmax)))
                        err_exit = True
                        continue

                    if len(valid) == 1:
                        err_exit = False
                        break

    if err_exit:
        return("Unknown")
    else:
        return(str(valid[-1]))


def check_commands(commands, ignore=''):
    """
    Check if command(s) can be called

    :param attributedict commands: dictionary of commands to check
    :param list ignore: list of commands that are optional and can be ignored
    """

    # Use `command` to see if command is callable, store exit code
    is_callable = True
    uncallable = []
    for name, command in commands.items():
        if command not in ignore:
            # if a command is a java file, modify the command
            if '.jar' in command:
                command = "java -jar " + command
            # if an environment variable is not expanded it means it points to
            # an uncallable command
            if '$' in command: 
                uncallable.append(command)

            code = os.system("command -v {0} >/dev/null 2>&1 || {{ exit 1; }}".format(command))
            # If exit code is not 0, track which command failed
            #print("{} code {}".format(command, code))  # DEBUG
            if code != 0:
                uncallable.append(command)
                is_callable = False
    if is_callable:
        return True
    else:
        print("The following required tool(s) are not callable: {0}".format(' '.join(uncallable)))
        return False


def calc_frip(bamfile, ftfile, frip_func, pipeline_manager,
              aligned_reads_key="Aligned_reads"):
    """
    Calculate the fraction of reads in feature file.

    Use the given function and data from an aligned reads file and a called
    peaks file, along with a PipelineManager, to calculate.

    :param str peakfile: path to called peaks file
    :param callable frip_func: how to calculate the fraction of reads in feat;
        this must accept the path to the aligned reads file and the path to
        the called peaks file as arguments.
    :param str bamfile: path to aligned reads file
    :param pypiper.PipelineManager pipeline_manager: the PipelineManager in use
        for the pipeline calling this function
    :param str aligned_reads_key: name of the key from a stats (key-value) file
        to use to fetch the count of aligned reads
    :return float: fraction of reads in peaks
    """
    frip_cmd = frip_func(bamfile, ftfile)
    num_in_reads = pipeline_manager.checkprint(frip_cmd)
    num_aligned_reads = pipeline_manager.get_stat(aligned_reads_key)
    print(num_aligned_reads, num_in_reads)
    return float(num_in_reads) / float(num_aligned_reads)


def _add_resources(args, res):
    """
    Add additional resources needed for pipeline.

    :param argparse.Namespace args: binding between option name and argument,
        e.g. from parsing command-line options
    :param pm.config.resources res: pipeline manager resources list
    """
    rgc = RGC(select_genome_config(res.get("genome_config")))
    # REQ
    for asset in ["chrom_sizes", BT2_IDX_KEY]:
        res[asset] = rgc.get_asset(args.genome_assembly, asset)

    # OPT
    msg = "The '{}' asset is not present in your REFGENIE config file."
    err = "The '{}' asset does not exist."

    asset = "tss_annotation"
    if args.TSS_name:        
        res[asset] = os.path.abspath(args.TSS_name)
    else:
        try:
            res[asset] = rgc.get_asset(args.genome_assembly, asset)
        except KeyError:
            print(msg.format(asset))
        except:            
            msg = ("Update your REFGENIE config file to include this asset, or "
                   "point directly to the file using --TSS-name.\n")
            print(err.format(asset))
            print(msg)

    asset = "pi_annotation"
    if args.PI_name:        
        res[asset] = os.path.abspath(args.PI_name)
    else:
        try:
            res[asset] = rgc.get_asset(args.genome_assembly, asset)
        except KeyError:
            print(msg.format(asset))
        except:            
            msg = ("Update your REFGENIE config file to include this asset, or "
                   "point directly to the file using --PI-name.\n")
            print(err.format(asset))
            print(msg)

    asset = "pre_mRNA_annotation"
    if args.pre_name:
        res[asset] = os.path.abspath(args.pre_name)
    else:
        try:
            res[asset] = rgc.get_asset(args.genome_assembly, asset)
        except KeyError:
            print(msg.format(asset))
        except:
            msg = ("Update your REFGENIE config file to include this asset, or "
                   "point directly to the file using --pre-name.\n")
            print(err.format(asset))
            print(msg)

    asset = "feat_annotation"
    if args.anno_name:
        res[asset] = os.path.abspath(args.anno_name)
    else:
        try:
            res[asset] = rgc.get_asset(args.genome_assembly, asset)
        except KeyError:
            print(msg.format(asset))
        except:
            msg = ("Update your REFGENIE config file to include this asset, or "
                   "point directly to the file using --anno-name.\n")
            print(err.format(asset))
            print(msg)

    asset = "exon_annotation"
    if args.exon_name:
        res[asset] = os.path.abspath(args.exon_name)
    else:
        try:
            res[asset] = rgc.get_asset(args.genome_assembly, asset)
        except KeyError:
            print(msg.format(asset))
        except:
            msg = ("Update your REFGENIE config file to include this asset, or "
                   "point directly to the file using --exon-name.\n")
            print(err.format(asset))
            print(msg)

    asset = "intron_annotation"
    if args.intron_name:
        res[asset] = os.path.abspath(args.intron_name)
    else:
        try:
            res[asset] = rgc.get_asset(args.genome_assembly, asset)
        except KeyError:
            print(msg.format(asset))
        except:
            msg = ("Update your REFGENIE config file to include this asset, or "
                   "point directly to the file using --intron-name.\n")
            print(err.format(asset))
            print(msg)

    res.rgc = rgc
    return res


###############################################################################
def main():
    """
    Main pipeline process.
    """

    args = parse_arguments()

    args.paired_end = args.single_or_paired == "paired"

    # Initialize, creating global PipelineManager and NGSTk instance for
    # access in ancillary functions outside of main().
    outfolder = os.path.abspath(
        os.path.join(args.output_parent, args.sample_name))
    global pm
    pm = pypiper.PipelineManager(
        name="PEPPRO", outfolder=outfolder, args=args, version=__version__)
    global ngstk
    ngstk = pypiper.NGSTk(pm=pm)

    # Convenience alias
    tools = pm.config.tools
    param = pm.config.parameters
    res   = pm.config.resources

    # Check that the required tools are callable by the pipeline
    tool_list = [v for k,v in tools.items()]    # extract tool list
    if args.trimmer == "fastx":  # update tool call
        tool_list = [t.replace('fastx', 'fastx_trimmer') for t in tool_list]
    else:  # otherwise remove it
        if 'fastx' in tool_list: tool_list.remove('fastx')

    if args.sob:
        tool_list = [t.replace('seqoutbias', 'seqOutBias') for t in tool_list]
    else:
        if 'seqoutbias' in tool_list: tool_list.remove('seqoutbias')

    tool_list = dict((t,t) for t in tool_list)  # convert back to dict

    if not check_commands(tool_list):
        err_msg = "Missing required tools. See message above."
        pm.fail_pipeline(RuntimeError(err_msg))

    if args.input2 and not args.paired_end:
        err_msg = "Incompatible settings: You specified single-end, but provided --input2."
        pm.fail_pipeline(RuntimeError(err_msg))

    # Set up reference resource according to genome prefix.
    res = _add_resources(args, res)

    # Get bowtie2 indexes
    _check_bowtie2_index(res.rgc, args.genome_assembly)
    for reference in args.prealignments:
        _check_bowtie2_index(res.rgc, reference)

    # Adapter file can be set in the config; if left null, we use a default.
    # TODO: use this option or just specify directly the adapter sequence as I do now
    res.adapters = res.adapters or tool_path("PRO-seq_adapter.fa")

    param.outfolder = outfolder

    # Check that the input file(s) exist before continuing
    if os.path.isfile(args.input[0]) and os.stat(args.input[0]).st_size > 0:
        print("Local input file: " + args.input[0])
    elif os.path.isfile(args.input[0]) and os.stat(args.input[0]).st_size == 0:
        # The read1 file exists but is empty
        err_msg = "File exists but is empty: {}"
        pm.fail_pipeline(IOError(err_msg.format(args.input[0])))
    else:
        # The read1 file does not exist
        err_msg = "Could not find: {}"
        pm.fail_pipeline(IOError(err_msg.format(args.input[0])))

    if args.input2:
        if os.path.isfile(args.input2[0]) and os.stat(args.input2[0]).st_size > 0:
            print("Local input file: " + args.input2[0])
        elif os.path.isfile(args.input2[0]) and os.stat(args.input2[0]).st_size == 0:
            # The read1 file exists but is empty
            err_msg = "File exists but is empty: {}"
            pm.fail_pipeline(IOError(err_msg.format(args.input2[0])))
        else:
            # The read1 file does not exist
            err_msg = "Could not find: {}"
            pm.fail_pipeline(IOError(err_msg.format(args.input2[0])))

    container = None

    ###########################################################################

    pm.report_result(
        "File_mb",
        round(ngstk.get_file_size(
            [x for x in [args.input, args.input2] if x is not None]), 2))
    pm.report_result("Read_type", args.single_or_paired)
    pm.report_result("Genome", args.genome_assembly)

    # PRO-seq pipeline
    # Each (major) step should have its own subfolder

    raw_folder = os.path.join(param.outfolder, "raw")
    fastq_folder = os.path.join(param.outfolder, "fastq")
    fastqc_folder=os.path.join(param.outfolder, "fastqc")
    ngstk.make_dir(fastqc_folder)

    pm.timestamp("### Merge/link and fastq conversion: ")
    # This command will merge multiple inputs so you can use multiple
    # sequencing lanes in a single pipeline run.
    local_input_files = ngstk.merge_or_link(
        [args.input, args.input2], raw_folder, args.sample_name)
    cmd, out_fastq_pre, unaligned_fastq = ngstk.input_to_fastq(
        local_input_files, args.sample_name, args.paired_end, fastq_folder)
    pm.run(cmd, unaligned_fastq,
           follow=ngstk.check_fastq(
               local_input_files, unaligned_fastq, args.paired_end),
           container=pm.container)
    pm.clean_add(out_fastq_pre + "*.fastq", conditional=True)
    print(local_input_files)
    untrimmed_fastq1 = out_fastq_pre + "_R1.fastq"
    untrimmed_fastq2 = out_fastq_pre + "_R2.fastq" if args.paired_end else None

    ###########################################################################
    # Begin fastq trimming
    ###########################################################################
    pm.timestamp("### FASTQ processing: ")

    # Create names for processed FASTQ files.
    noadap_fastq = os.path.join(
        fastq_folder, args.sample_name + "_R1_noadap.fastq")
    # noadap_fastq_R2 = os.path.join(
    #     fastq_folder, args.sample_name + "_R2_noadap.fastq")
    dedup_fastq = os.path.join(
        fastq_folder, args.sample_name + "_R1_dedup.fastq")
    trimmed_fastq = os.path.join(
        fastq_folder, args.sample_name + "_R1_trimmed.fastq")
    processed_fastq = os.path.join(
        fastq_folder, args.sample_name + "_R1_processed.fastq")
    
    adapter_html = os.path.join(
        fastqc_folder, args.sample_name + "_rmAdapter.html")
    adapter_json = os.path.join(
        fastqc_folder, args.sample_name + "_rmAdapter.json")
    adapter_report = os.path.join(
        fastqc_folder, args.sample_name + "_rmAdapter.txt")
    umi_report = os.path.join(
        fastqc_folder, args.sample_name + "_rmUmi.html")
    umi_json = os.path.join(
        fastqc_folder, args.sample_name + "_rmUmi.json")

    # Check quality encoding for use with FastX_Tools
    if args.trimmer == "fastx":
        encoding = guess_encoding(untrimmed_fastq1)
        #print("Encoding: {}".format(str(encoding)))  # DEBUG
        
    # Create adapter trimming command(s).
    if args.adapter == "fastp":
        if args.paired_end:
            adapter_cmd_chunks = [
                ("(" + tools.fastp),
                ("--thread", str(pm.cores)),
                ("--in1", untrimmed_fastq1),
                ("--in2", untrimmed_fastq2),
                ("--adapter_sequence", "TGGAATTCTCGGGTGCCAAGG"),
                ("--length_required", (18 + int(float(args.umi_len)))),
                ("--html", adapter_html),
                ("--json", adapter_json)
            ]
        else:
            adapter_cmd_chunks = [
                ("(" + tools.fastp),
                ("--thread", str(pm.cores)),
                ("--in1", untrimmed_fastq1),
                ("--adapter_sequence", "TGGAATTCTCGGGTGCCAAGG"),
                ("--length_required", (18 + int(float(args.umi_len)))),
                ("--html", adapter_html),
                ("--json", adapter_json)
            ]

        if args.complexity:
            if args.paired_end:
                adapter_cmd_chunks.extend([("--stdout")])
                adapter_cmd_chunks.extend([(">", noadap_fastq)])
            else:
                adapter_cmd_chunks.extend([("-o", noadap_fastq)])
        else:
            adapter_cmd_chunks.extend([("--stdout")])

        adapter_cmd_chunks.extend([
            (") 2>", adapter_report)
        ])

        adapter_cmd = build_command(adapter_cmd_chunks)

    elif args.adapter == "cutadapt":
        cut_version = float(pm.checkprint("cutadapt --version"))

        adapter_cmd_chunks = ["(" + tools.cutadapt]
        # old versions of cutadapt can not use multiple cores
        if cut_version >= 1.15:
            adapter_cmd_chunks.extend([("-j", str(pm.cores))])

        adapter_cmd_chunks.extend([
                ("-m", (18 + int(float(args.umi_len)))),
                ("-a", "TGGAATTCTCGGGTGCCAAGG")])

        if args.paired_end:
            adapter_cmd_chunks.extend([
                "--interleaved",               
                untrimmed_fastq1,
                untrimmed_fastq2
            ])
        else:
            adapter_cmd_chunks.extend([
                untrimmed_fastq1
            ])

        if args.complexity:
            adapter_cmd_chunks.extend([
                ("-o", noadap_fastq + ")"),
                (">", adapter_report)
            ])

        adapter_cmd = build_command(adapter_cmd_chunks)

    else:
        # Default to fastp
        if args.paired_end:
            adapter_cmd_chunks = [
                ("(" + tools.fastp),
                ("--thread", str(pm.cores)),
                ("--in1", untrimmed_fastq1),
                ("--in2", untrimmed_fastq2),
                ("--adapter_sequence", "TGGAATTCTCGGGTGCCAAGG"),
                ("--length_required", (18 + int(float(args.umi_len)))),
                ("--html", adapter_html),
                ("--json", adapter_json)
            ]
        else:
            adapter_cmd_chunks = [
                ("(" + tools.fastp),
                ("--thread", str(pm.cores)),
                ("--in1", untrimmed_fastq1),
                ("--adapter_sequence", "TGGAATTCTCGGGTGCCAAGG"),
                ("--length_required", (18 + int(float(args.umi_len)))),
                ("--html", adapter_html),
                ("--json", adapter_json)
            ]

        if args.complexity:
            if args.paired_end:
                adapter_cmd_chunks.extend([("--stdout")])
                adapter_cmd_chunks.extend([(">", noadap_fastq)])
            else:
                adapter_cmd_chunks.extend([("-o", noadap_fastq)])
        else:
            adapter_cmd_chunks.extend([("--stdout")])

        adapter_cmd_chunks.extend([
            (") 2>", adapter_report)
        ])

        adapter_cmd = build_command(adapter_cmd_chunks)

    # Create deduplication command(s).
    if args.dedup == "seqkit":
        dedup_cmd_chunks = [
            (tools.seqkit, "rmdup"),
            ("--threads", str(pm.cores)),
            "--by-seq",
            "--ignore-case",
            "-o"           
        ]
        if args.complexity:
            dedup_cmd_chunks.extend([
                (dedup_fastq, noadap_fastq)
            ])
        else:
            dedup_cmd_chunks.extend(["-"])

        dedup_cmd = build_command(dedup_cmd_chunks)

    elif args.dedup == "fqdedup":
        dedup_cmd_chunks = [tools.fqdedup]
        if args.complexity:
            dedup_cmd_chunks.extend([("-i", noadap_fastq)])
            dedup_cmd_chunks.extend([("-o", dedup_fastq)])
        else:
            dedup_cmd_chunks.extend([("-i", "-")])
            dedup_cmd_chunks.extend([("-o", "-")])

        dedup_cmd = build_command(dedup_cmd_chunks)

    else:
        # Default to seqkit
        dedup_cmd_chunks = [
            (tools.seqkit, "rmdup"),
            ("--threads", str(pm.cores)),
            "--by-seq",
            "--ignore-case",
            "-o"           
        ]
        if args.complexity:
            dedup_cmd_chunks.extend([
                (dedup_fastq, noadap_fastq)
            ])
        else:
            dedup_cmd_chunks.extend(["-"])

        dedup_cmd = build_command(dedup_cmd_chunks)

    # Create trimming and reverse complementing command(s).
    # TODO: Can also use seqkit for these steps instead of seqtk...
    if args.umi:
        if args.adapter != "fastp":
            print("To remove UMI intelligently, you must process your reads using 'fastp'")
            print("Defaulting to removing the first {} "
                  "bp instead via trimming".format(str(args.umi_len)))
            if args.trimmer == "seqtk":
                trim_cmd_chunks = [
                    tools.seqtk,
                    "trimfq",
                    ("-b", str(args.umi_len))
                ]

                if args.max_len != -1:
                    trim_cmd_chunks.extend([
                        ("-L", str(args.max_len))
                    ])

                if args.complexity:
                    # Need undeduplicated results for complexity calculation
                    #trim_cmd_chunks2 = trim_cmd_chunks.copy()  # python3
                    trim_cmd_chunks2 = list(trim_cmd_chunks)
                    trim_cmd_chunks2.extend([noadap_fastq])
                    trim_cmd_chunks.extend([dedup_fastq])
                else:
                    trim_cmd_chunks.extend(["-"])

                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        (">", processed_fastq)                        
                    ])
                    trim_cmd_chunks2.extend([
                        (">", trimmed_fastq)                        
                    ])
                else:
                    trim_cmd_chunks.extend(["|"])
                    trim_cmd_chunks.extend([
                        tools.seqtk,
                        ("seq", "-r"),
                        ("-", ">"),
                        processed_fastq
                    ])
                    trim_cmd_chunks2.extend(["|"])
                    trim_cmd_chunks2.extend([
                        tools.seqtk,
                        ("seq", "-r"),
                        ("-", ">"),
                        trimmed_fastq
                    ])

            elif args.trimmer == "fastx":
                trim_tool = tools.fastx + "_trimmer"
                rc_tool = tools.fastx + "_reverse_complement"
                trim_cmd_chunks = [trim_tool]

                if encoding == "Illumina-1.8":
                    trim_cmd_chunks.extend([
                        ("-Q", str(33))
                    ])
                trim_cmd_chunks.extend([
                    ("-f", str(int(float(args.umi_len)) + 1))
                ])
                if args.max_len != -1:
                    trim_cmd_chunks.extend([
                        ("-l", (str(int(float(args.max_len)) + int(float(args.umi_len)))))
                    ])

                if args.complexity:
                    # Need undeduplicated results for complexity calculation
                    #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                    trim_cmd_chunks2 = list(trim_cmd_chunks)
                    trim_cmd_chunks2.extend([
                        ("-i", noadap_fastq)
                    ])
                    trim_cmd_chunks.extend([
                        ("-i", dedup_fastq)
                    ])

                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        ("-o", processed_fastq)                        
                    ])
                    trim_cmd_chunks2.extend([
                        ("-o", trimmed_fastq)                        
                    ])
                else:
                    trim_cmd_chunks.extend([
                        ("|", rc_tool)
                    ])
                    if encoding == "Illumina-1.8":
                        trim_cmd_chunks.extend([
                            ("-Q", str(33))
                        ])
                        trim_cmd_chunks2.extend([
                            ("-Q", str(33))
                        ])
                    trim_cmd_chunks.extend([
                        ("-o", processed_fastq)
                    ])
                    trim_cmd_chunks2.extend([
                        ("-o", trimmed_fastq)
                    ])

            else:
                # Default to seqtk
                trim_cmd_chunks = [
                    tools.seqtk,
                    "trimfq",
                    ("-b", str(args.umi_len))
                ]

                if args.max_len != -1:
                    trim_cmd_chunks.extend([
                        ("-L", str(args.max_len))
                    ])

                if args.complexity:
                    trim_cmd_chunks.extend([dedup_fastq])
                    # Need undeduplicated results for complexity calculation
                    #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                    trim_cmd_chunks2 = list(trim_cmd_chunks)
                    trim_cmd_chunks2.extend([noadap_fastq])
                else:
                    trim_cmd_chunks.extend(["-"])

                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        (">", processed_fastq)                        
                    ])
                    trim_cmd_chunks2.extend([
                        (">", trimmed_fastq)                        
                    ])
                else:
                    trim_cmd_chunks.extend(["|"])
                    trim_cmd_chunks.extend([
                        tools.seqtk,
                        ("seq", "-r"),
                        ("-", ">"),
                        processed_fastq
                    ])
                    trim_cmd_chunks2.extend(["|"])
                    trim_cmd_chunks2.extend([
                        tools.seqtk,
                        ("seq", "-r"),
                        ("-", ">"),
                        trimmed_fastq
                    ])

        else:
            if args.paired_end:
                if args.complexity:
                    trim_cmd_chunks = [
                        tools.fastp,
                        ("--thread", str(pm.cores))
                    ]
                    #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                    trim_cmd_chunks2 = list(trim_cmd_chunks)
                    trim_cmd_chunks.extend([
                        ("-i", dedup_fastq),
                        "--stdout",
                        "--interleaved_in",
                        "--umi",
                        ("--umi_loc", "read1"),
                        ("--umi_len", args.umi_len),
                        ("--html", umi_report),
                        ("--json", umi_json),
                        "|",
                        (tools.seqtk, "trimfq")
                    ])
                    if args.max_len != -1:
                        trim_cmd_chunks.extend([
                            ("-L", args.max_len)
                        ])
                    trim_cmd_chunks.extend(["-"])
                    if args.runon.lower() == "gro":
                        trim_cmd_chunks.extend([
                            (">", processed_fastq)                        
                        ])
                    else:
                        trim_cmd_chunks.extend([
                            "|",
                            (tools.seqtk, "seq"),
                            ("-r", "-"),
                            (">", processed_fastq)
                        ])
                    trim_cmd_chunks2.extend([
                        ("-i", noadap_fastq),
                        "--stdout",
                        "--interleaved_in",
                        "--umi",
                        ("--umi_loc", "read1"),
                        ("--umi_len", args.umi_len),
                        ("--html", umi_report),
                        ("--json", umi_json),
                        "|",
                        (tools.seqtk, "trimfq")
                    ])
                    if args.max_len != -1:
                        trim_cmd_chunks2.extend([
                            ("-L", args.max_len)
                        ])
                    trim_cmd_chunks2.extend(["-"])
                    if args.runon.lower() == "gro":
                        trim_cmd_chunks2.extend([
                            (">", trimmed_fastq)                        
                        ])
                    else:
                        trim_cmd_chunks2.extend([
                            "|",
                            (tools.seqtk, "seq"),
                            ("-r", "-"),
                            (">", trimmed_fastq)
                        ])

                else:
                    trim_cmd_chunks = [
                        tools.fastp,
                        ("--thread", str(pm.cores)),
                        ("--stdin", "--stdout"),
                        "--interleaved_in",
                        "--umi",
                        ("--umi_loc", "read1"),
                        ("--umi_len", args.umi_len),
                        ("--html", umi_report),
                        ("--json", umi_json),
                        "|",
                        (tools.seqtk, "trimfq")
                    ]
                    if args.max_len != -1:
                        trim_cmd_chunks.extend([
                            ("-L", args.max_len)
                        ])
                    trim_cmd_chunks.extend(["-"])
                    if args.runon.lower() == "gro":
                        trim_cmd_chunks.extend([
                            (">", processed_fastq)                        
                        ])
                    else:
                        trim_cmd_chunks.extend([
                            "|",
                            (tools.seqtk, "seq"),
                            ("-r", "-"),
                            (">", processed_fastq)                        
                        ])

            else:
                if args.complexity:
                    trim_cmd_chunks = [
                        tools.fastp,
                        ("--thread", str(pm.cores))
                    ]
                    #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                    trim_cmd_chunks2 = list(trim_cmd_chunks)
                    trim_cmd_chunks2.extend([
                        ("-i", noadap_fastq),
                        "--stdout",
                        "--umi",
                        ("--umi_loc", "read1"),
                        ("--umi_len", args.umi_len),
                        ("--html", umi_report),
                        ("--json", umi_json),
                        "|",
                        (tools.seqtk, "trimfq")
                    ])
                    if args.max_len != -1:
                        trim_cmd_chunks2.extend([
                            ("-L", args.max_len)
                        ])
                    trim_cmd_chunks2.extend(["-"])
                    if args.runon.lower() == "gro":
                        trim_cmd_chunks2.extend([
                            (">", trimmed_fastq)
                        ])
                    else:
                        trim_cmd_chunks2.extend([
                            "|",
                            (tools.seqtk, "seq"),
                            ("-r", "-"),
                            (">", trimmed_fastq)
                        ])
                    trim_cmd_chunks.extend([
                        ("-i", dedup_fastq),
                        "--stdout",
                        "--umi",
                        ("--umi_loc", "read1"),
                        ("--umi_len", args.umi_len),
                        ("--html", umi_report),
                        ("--json", umi_json),
                        "|",
                        (tools.seqtk, "trimfq")
                    ])
                    if args.max_len != -1:
                        trim_cmd_chunks.extend([
                            ("-L", args.max_len)
                        ])
                    trim_cmd_chunks.extend(["-"])
                    if args.runon.lower() == "gro":
                        trim_cmd_chunks.extend([
                            (">", processed_fastq)
                        ])
                    else:
                        trim_cmd_chunks.extend([
                            "|",
                            (tools.seqtk, "seq"),
                            ("-r", "-"),
                            (">", processed_fastq)
                        ])

                else:
                    trim_cmd_chunks = [
                        tools.fastp,
                        ("--thread", str(pm.cores)),
                        ("--stdin", "--stdout"),
                        "--umi",
                        ("--umi_loc", "read1"),
                        ("--umi_len", args.umi_len),
                        ("--html", umi_report),
                        ("--json", umi_json),
                        "|",
                        (tools.seqtk, "trimfq")
                    ]
                    if args.max_len != -1:
                        trim_cmd_chunks.extend([
                            ("-L", args.max_len)
                        ])
                    trim_cmd_chunks.extend(["-"])
                    if args.runon.lower() == "gro":
                        trim_cmd_chunks.extend([
                            (">", processed_fastq)
                        ])
                    else:
                        trim_cmd_chunks.extend([
                            "|",
                            (tools.seqtk, "seq"),
                            ("-r", "-"),
                            (">", processed_fastq)
                        ])

    else:
        if args.trimmer == "seqtk":
            trim_cmd_chunks = [
                tools.seqtk,
                "trimfq",
                ("-b", str(args.umi_len))
            ]              
            if args.max_len != -1:
                trim_cmd_chunks.extend([
                    ("-L", str(args.max_len))
                ])
            if args.complexity:
                #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                trim_cmd_chunks2 = list(trim_cmd_chunks)
                trim_cmd_chunks2.extend([noadap_fastq])
                if args.runon.lower() == "gro":
                    trim_cmd_chunks2.extend([
                        (">", trimmed_fastq)
                    ])
                else:
                    trim_cmd_chunks2.extend([
                        "|",
                        (tools.seqtk, "seq"),
                        ("-r", "-"),
                        (">", trimmed_fastq)
                    ])
                trim_cmd_chunks.extend([dedup_fastq])
                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        (">", processed_fastq)
                    ])
                else:
                    trim_cmd_chunks.extend([
                        "|",
                        (tools.seqtk, "seq"),
                        ("-r", "-"),
                        (">", processed_fastq)
                    ])
            else:
                trim_cmd_chunks.extend(["-"])
                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        (">", processed_fastq)
                    ])
                else:
                    trim_cmd_chunks.extend([
                        "|",
                        (tools.seqtk, "seq"),
                        ("-r", "-"),
                        (">", processed_fastq)
                    ])

        elif args.trimmer == "fastx":
            trim_tool = tools.fastx + "_trimmer"
            rc_tool = tools.fastx + "_reverse_complement"
            trim_cmd_chunks = [trim_tool]
            if encoding == "Illumina-1.8":
                trim_cmd_chunks.extend([
                    ("-Q", str(33))
                ])
            trim_cmd_chunks.extend([
                ("-f", str(int(float(args.umi_len)) + 1))
            ])
            if args.max_len != -1:
                trim_cmd_chunks.extend([
                    ("-l", (str(int(float(args.max_len)) + int(float(args.umi_len)))))
                ])
            if args.complexity:
                # Need undeduplicated results for complexity calculation
                #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                trim_cmd_chunks2 = list(trim_cmd_chunks)
                trim_cmd_chunks2.extend([
                    ("-i", noadap_fastq)
                ])
                trim_cmd_chunks.extend([
                    ("-i", dedup_fastq)
                ])
            if args.runon.lower() == "gro":
                trim_cmd_chunks.extend([
                    ("-o", processed_fastq)
                ])
                trim_cmd_chunks2.extend([
                    ("-o", trimmed_fastq)
                ])
            else:
                trim_cmd_chunks.extend([
                    ("|", rc_tool)
                ])
                if encoding == "Illumina-1.8":
                    trim_cmd_chunks.extend([
                        ("-Q", str(33))
                    ])
                    trim_cmd_chunks2.extend([
                        ("-Q", str(33))
                    ])
                trim_cmd_chunks.extend([
                    ("-o", processed_fastq)
                ])
                trim_cmd_chunks2.extend([
                    ("-o", trimmed_fastq)
                ])

        else:
            # Default to seqtk
            trim_cmd_chunks = [
                tools.seqtk,
                "trimfq",
                ("-b", str(args.umi_len))
            ]              
            if args.max_len != -1:
                trim_cmd_chunks.extend([
                    ("-L", str(args.max_len))
                ])
            if args.complexity:
                #trim_cmd_chunks2 = trim_cmd_chunks.copy()  #python3
                trim_cmd_chunks2 = list(trim_cmd_chunks)
                trim_cmd_chunks2.extend([noadap_fastq])
                if args.runon.lower() == "gro":
                    trim_cmd_chunks2.extend([
                        (">", trimmed_fastq)
                    ])
                else:
                    trim_cmd_chunks2.extend([
                        "|",
                        (tools.seqtk, "seq"),
                        ("-r", "-"),
                        (">", trimmed_fastq)
                    ])
                trim_cmd_chunks.extend([dedup_fastq])
                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        (">", processed_fastq)
                    ])
                else:
                    trim_cmd_chunks.extend([
                        "|",
                        (tools.seqtk, "seq"),
                        ("-r", "-"),
                        (">", processed_fastq)
                    ])
            else:
                trim_cmd_chunks.extend(["-"])
                if args.runon.lower() == "gro":
                    trim_cmd_chunks.extend([
                        (">", processed_fastq)
                    ])
                else:
                    trim_cmd_chunks.extend([
                        "|",
                        (tools.seqtk, "seq"),
                        ("-r", "-"),
                        (">", processed_fastq)
                    ])

    trim_cmd = build_command(trim_cmd_chunks)
    if args.complexity:
        trim_cmd2 = build_command(trim_cmd_chunks2)

    def report_fastq():
        """
        Report QC metrics on intermediate steps of fastq file preparation
        """
        if args.adapter == "fastp":
            adapter_term = "reads with adapter trimmed:"
            too_short_term = "reads failed due to too short:"
            total_bases_term = "total bases:"

            ac_cmd = ("grep '" + adapter_term + "' " +
                       adapter_report + " | head -n 1 | awk '{print $NF}'")
            ts_cmd = ("grep '" + too_short_term + "' " +
                       adapter_report + " | head -n 1 | awk '{print $NF}'")
            bases = ("grep '" + total_bases_term + "' " +
                     adapter_report + " | head -n 1 | awk '{print $NF}'")
            adapter_bases = ("grep 'bases trimmed due to adapters:' " +
                             adapter_report + " | awk '{print $NF}'")

            pm.report_object("FastP_report", adapter_html)

        elif args.adapter == "cutadapt":
            adapter_term = "Reads with adapters:"
            too_short_term = "Reads that were too short:"
            total_bases_term = "Total basepairs processed:"

            ac_cmd = ("grep '" + adapter_term + "' " +
                      adapter_report + " | awk '{print $(NF-1)}'")
            ts_cmd = ("grep '" + too_short_term + "' " +
                      adapter_report + " | awk '{print $(NF-1)}'")
            bases = ("grep '" + total_bases_term + "' " +
                     adapter_report + " | awk '{print $(NF-1)}'")
            adapter_bases = ("awk '{sum+=$1*$2} END {printf \"%.0f\", sum}' " +
                             adapter_report)

        else:  # default to fastp
            adapter_term = "reads with adapter trimmed:"
            too_short_term = "reads failed due to too short:"
            total_bases_term = "total bases:"

            ac_cmd = ("grep '" + adapter_term + "' " +
                       adapter_report + " | head -n 1 | awk '{print $NF}'")
            ts_cmd = ("grep '" + too_short_term + "' " +
                       adapter_report + " | head -n 1 | awk '{print $NF}'")
            bases = ("grep '" + total_bases_term + "' " +
                     adapter_report + " | head -n 1 | awk '{print $NF}'")
            adapter_bases = ("grep 'bases trimmed due to adapters:' " +
                             adapter_report + " | awk '{print $NF}'")

            pm.report_object("FastP_report", adapter_html)

        ac = float(pm.checkprint(ac_cmd).replace(',',''))
        pm.report_result("Reads_with_adapter", ac)
        total_bases = float(pm.checkprint(bases).replace(',',''))
        total_adapter = float(pm.checkprint(adapter_bases).replace(',',''))
        pm.report_result("Pct_adapter_contamination",
                         round(float(total_adapter/total_bases), 2))

        ts = float(pm.checkprint(ts_cmd).replace(',',''))
        pm.report_result("Reads_too_short", ts)

        tr = int(ngstk.count_lines(noadap_fastq).strip())
        dr = int(ngstk.count_lines(dedup_fastq).strip())
        dups = max(0, (float(tr)/4 - float(dr)/4))
        pm.report_result("Duplicate_reads", dups)

    # Put it all together
    if args.complexity:
        pm.run([adapter_cmd, dedup_cmd, trim_cmd2],
               trimmed_fastq, follow=report_fastq)
        pm.run(trim_cmd, processed_fastq,
               follow=ngstk.check_trim(processed_fastq, False, None))
        pm.clean_add(noadap_fastq)
        pm.clean_add(dedup_fastq)
        pm.clean_add(trimmed_fastq)
    else:
        process_fastq_cmd = build_command([
            adapter_cmd, "|", dedup_cmd, "|", trim_cmd])
        pm.run(process_fastq_cmd, processed_fastq,
           follow=ngstk.check_trim(processed_fastq, False, None))

    pm.clean_add(os.path.join(fastq_folder, "*.fq"), conditional=True)
    pm.clean_add(os.path.join(fastq_folder, "*.log"), conditional=True)
    ###########################################################################
    # End fastq processing
    ###########################################################################

    # TODO: Check if all reads were trimmed
    
    ###########################################################################
    # Deinterleave if paired-end
    ###########################################################################
    
    if args.paired_end:
        pm.timestamp("### Deinterleave processed FASTQ files")
        
        if args.complexity:
            interleaved_dups = os.path.join(
                fastq_folder, args.sample_name + "_interleaved_dups.fastq")
            deinterleave_prefix_dups = os.path.join(
                fastq_folder, args.sample_name + "_deinterleave_dups")
            deinterleave_fq1_dups = os.path.join(
                fastq_folder, args.sample_name + "_deinterleave_dups_R1.fastq")
            deinterleave_fq2_dups = os.path.join(
                fastq_folder, args.sample_name + "_deinterleave_dups_R2.fastq")

            unmap_fq1_dups = deinterleave_fq1_dups
            unmap_fq2_dups = deinterleave_fq2_dups

            cmd1 = (tools.seqtk + " dropse " + trimmed_fastq +
                    " > " + interleaved_dups)
            cmd2 = (tool_path('fastq_split.py') +
                    " -i " + interleaved_dups +
                    " -o " + deinterleave_prefix_dups)
            pm.run([cmd1, cmd2], [deinterleave_fq1_dups, deinterleave_fq2_dups])
            pm.clean_add(interleaved_dups)
            pm.clean_add(unmap_fq1_dups)
            pm.clean_add(unmap_fq2_dups)
            pm.clean_add(deinterleave_fq1_dups)
            pm.clean_add(deinterleave_fq2_dups)

        interleaved = os.path.join(
                fastq_folder, args.sample_name + "_interleaved.fastq")
        deinterleave_prefix = os.path.join(
                fastq_folder, args.sample_name + "_deinterleave")
        deinterleave_fq1 = os.path.join(
            fastq_folder, args.sample_name + "_deinterleave_R1.fastq")
        deinterleave_fq2 = os.path.join(
            fastq_folder, args.sample_name + "_deinterleave_R2.fastq")

        cmd1 = (tools.seqtk + " dropse " + processed_fastq +
                " > " + interleaved)
        cmd2 = (tool_path('fastq_split.py') +
                " -i " + interleaved +
                " -o " + deinterleave_prefix)
        pm.run([cmd1, cmd2], [deinterleave_fq1, deinterleave_fq2])
        pm.clean_add(interleaved)
        pm.clean_add(deinterleave_fq1)
        pm.clean_add(deinterleave_fq2)

        # Prepare variables for alignment step
        unmap_fq1 = deinterleave_fq1
        unmap_fq2 = deinterleave_fq2
        
    else:
        if args.complexity:
            unmap_fq1 = processed_fastq
            unmap_fq1_dups = trimmed_fastq
            unmap_fq2 = ""
            unmap_fq2_dups = ""
        else:
            unmap_fq1 = processed_fastq
            unmap_fq2 = ""  

    # Map to any requested prealignments
    # We recommend mapping to chrM first for PRO-seq data
    pm.timestamp("### Prealignments")
    to_compress = []
    if len(args.prealignments) == 0:
        print("You may use `--prealignments` to align to references before "
              "the genome alignment step. See docs.")
    else:
        print("Prealignment assemblies: " + str(args.prealignments))
        # Loop through any prealignment references and map to them sequentially
        for reference in args.prealignments:
            if args.complexity:
                if args.no_fifo:
                    unmap_fq1, unmap_fq2 = _align_with_bt2(
                    args, tools, args.paired_end, False, unmap_fq1,
                    unmap_fq2, reference,
                    assembly_bt2=os.path.join(
                        res.rgc.get_asset(reference, BT2_IDX_KEY), reference),
                    outfolder=param.outfolder,
                    aligndir="prealignments")

                    unmap_fq1_dups, unmap_fq2_dups = _align_with_bt2(
                    args, tools, args.paired_end, False, unmap_fq1_dups,
                    unmap_fq2_dups, reference,
                    assembly_bt2=os.path.join(
                        res.rgc.get_asset(reference, BT2_IDX_KEY), reference),
                    outfolder=param.outfolder,
                    aligndir="prealignments",
                    dups=True)
                    
                else:
                    unmap_fq1, unmap_fq2 = _align_with_bt2(
                    args, tools, args.paired_end, True, unmap_fq1,
                    unmap_fq2, reference,
                    assembly_bt2=os.path.join(
                        res.rgc.get_asset(reference, BT2_IDX_KEY), reference),
                    outfolder=param.outfolder,
                    aligndir="prealignments")

                    unmap_fq1_dups, unmap_fq2_dups = _align_with_bt2(
                    args, tools, args.paired_end, True, unmap_fq1_dups,
                    unmap_fq2_dups, reference,
                    assembly_bt2=os.path.join(
                        res.rgc.get_asset(reference, BT2_IDX_KEY), reference),
                    outfolder=param.outfolder,
                    aligndir="prealignments",
                    dups=True)
                if args.paired_end:
                    to_compress.append(unmap_fq1_dups)
                    to_compress.append(unmap_fq2_dups)
                    to_compress.append(unmap_fq1)
                    to_compress.append(unmap_fq2)
                else:
                    to_compress.append(unmap_fq1_dups)
                    to_compress.append(unmap_fq1)
            else:
                if args.no_fifo:
                    unmap_fq1, unmap_fq2 = _align_with_bt2(
                    args, tools, args.paired_end, False,
                    unmap_fq1, unmap_fq2, reference,
                    assembly_bt2=os.path.join(
                        res.rgc.get_asset(reference, BT2_IDX_KEY), reference),
                    outfolder=param.outfolder,
                    aligndir="prealignments")
                else:
                    unmap_fq1, unmap_fq2 = _align_with_bt2(
                    args, tools, args.paired_end, True,
                    unmap_fq1, unmap_fq2, reference,
                    assembly_bt2=os.path.join(
                        res.rgc.get_asset(reference, BT2_IDX_KEY), reference),
                    outfolder=param.outfolder,
                    aligndir="prealignments")
                if args.paired_end:
                    to_compress.append(unmap_fq1)
                    to_compress.append(unmap_fq2)
                else:
                    to_compress.append(unmap_fq1)

    pm.timestamp("### Map to genome")
    map_genome_folder = os.path.join(
        param.outfolder, "aligned_" + args.genome_assembly)
    ngstk.make_dir(map_genome_folder)

    mapping_genome_bam = os.path.join(
        map_genome_folder, args.sample_name + "_sort.bam")
    mapping_genome_bam_temp = os.path.join(
        map_genome_folder, args.sample_name + "_temp.bam")
    failQC_genome_bam = os.path.join(
        map_genome_folder, args.sample_name + "_fail_qc.bam")
    unmap_genome_bam = os.path.join(
        map_genome_folder, args.sample_name + "_unmap.bam")

    if args.complexity:
        mapping_genome_bam_dups = os.path.join(
            map_genome_folder, args.sample_name + "_sort_dups.bam")
        mapping_genome_bam_temp_dups = os.path.join(
            map_genome_folder, args.sample_name + "_temp_dups.bam")
        failQC_genome_bam_dups = os.path.join(
            map_genome_folder, args.sample_name + "_fail_qc_dups.bam")
        unmap_genome_bam_dups = os.path.join(
            map_genome_folder, args.sample_name + "_unmap_dups.bam")

    bt2_options = " --very-sensitive"
    bt2_options += " -X 2000"

    # samtools sort needs a temporary directory
    tempdir = tempfile.mkdtemp(dir=map_genome_folder)
    pm.clean_add(tempdir)

    # check input for zipped or not
    if pypiper.is_gzipped_fastq(unmap_fq1):
        cmd = (ngstk.ziptool + " -d " + (unmap_fq1 + ".gz"))
        pm.run(cmd, mapping_genome_bam)
    if args.paired_end:
        if pypiper.is_gzipped_fastq(unmap_fq2):
            cmd = (ngstk.ziptool + " -d " + (unmap_fq2 + ".gz"))
            pm.run(cmd, mapping_genome_bam)

    cmd = tools.bowtie2 + " -p " + str(pm.cores)
    cmd += bt2_options
    cmd += " --rg-id " + args.sample_name
    cmd += " -x " + os.path.join(
        res.rgc.get_asset(args.genome_assembly, BT2_IDX_KEY),
                          args.genome_assembly)
    if args.paired_end:
        cmd += " -1 " + unmap_fq1 + " -2 " + unmap_fq2
    else:
        cmd += " -U " + unmap_fq1
    cmd += " | " + tools.samtools + " view -bS - -@ 1 "
    cmd += " | " + tools.samtools + " sort - -@ 1"
    cmd += " -T " + tempdir
    cmd += " -o " + mapping_genome_bam_temp

    if args.complexity:
        # check input for zipped or not
        if pypiper.is_gzipped_fastq(unmap_fq1_dups):
            cmd = (ngstk.ziptool + " -d " + (unmap_fq1_dups + ".gz"))
            pm.run(cmd, mapping_genome_bam)
        if args.paired_end:
            if pypiper.is_gzipped_fastq(unmap_fq2_dups):
                cmd = (ngstk.ziptool + " -d " + (unmap_fq2_dups + ".gz"))
                pm.run(cmd, mapping_genome_bam)

        cmd_dups = tools.bowtie2 + " -p " + str(pm.cores)
        cmd_dups += bt2_options
        cmd_dups += " --rg-id " + args.sample_name
        cmd_dups += " -x " + os.path.join(
            res.rgc.get_asset(args.genome_assembly, BT2_IDX_KEY),
                              args.genome_assembly)
        if args.paired_end:
            cmd_dups += " -1 " + unmap_fq1_dups + " -2 " + unmap_fq2_dups
        else:
            cmd_dups += " -U " + unmap_fq1_dups
        cmd_dups += " | " + tools.samtools + " view -bS - -@ 1 "
        cmd_dups += " | " + tools.samtools + " sort - -@ 1"
        cmd_dups += " -T " + tempdir
        cmd_dups += " -o " + mapping_genome_bam_temp_dups

    # Split genome mapping result bamfile into two: high-quality aligned
    # reads (keepers) and unmapped reads (in case we want to analyze the
    # altogether unmapped reads)
    # -q 10: skip alignments with MAPQ less than 10
    cmd2 = (tools.samtools + " view -q 10 -b -@ " + str(pm.cores) +
            " -U " + failQC_genome_bam + " ")
    #if args.paired_end:
        # add a step to accept only reads mapped in proper pair
        # ?not appropriate with reverse complemented proseq reads?
        #cmd2 += "-f 2 "

    cmd2 += mapping_genome_bam_temp + " > " + mapping_genome_bam

    if args.complexity:
        cmd2_dups = (tools.samtools + " view -q 10 -b -@ " + str(pm.cores) +
            " -U " + failQC_genome_bam_dups + " ")
        #if args.paired_end:
            # add a step to accept only reads mapped in proper pair
            #cmd2_dups += "-f 2 "

        cmd2_dups += mapping_genome_bam_temp_dups + " > " + mapping_genome_bam_dups
        pm.clean_add(failQC_genome_bam_dups)

    def check_alignment_genome(temp_bam, bam):
        mr = ngstk.count_mapped_reads(temp_bam, args.paired_end)
        ar = ngstk.count_mapped_reads(bam, args.paired_end)
        rr = float(pm.get_stat("Raw_reads"))
        tr = float(pm.get_stat("Trimmed_reads"))
        if os.path.exists(res.pre_mRNA_annotation):
            cmd = (tools.samtools + " depth -b " +
                   res.pre_mRNA_annotation + " " + bam +
                   " | awk '{counter++;sum+=$3}END{print sum/counter}'")
            rd = pm.checkprint(cmd)
        else:
            cmd = (tools.samtools + " depth " + bam +
                   " | awk '{counter++;sum+=$3}END{print sum/counter}'")
            rd = pm.checkprint(cmd)
        pm.report_result("Mapped_reads", mr)
        pm.report_result("QC_filtered_reads",
                         round(float(mr)) - round(float(ar)))
        pm.report_result("Aligned_reads", ar)
        pm.report_result("Alignment_rate", round(float(ar) * 100 /
                         float(tr), 2))
        pm.report_result("Total_efficiency", round(float(ar) * 100 /
                         float(rr), 2))
        if rd and rd.strip():
            pm.report_result("Read_depth", round(float(rd), 2))

    pm.run([cmd, cmd2], mapping_genome_bam,
           follow=lambda: check_alignment_genome(mapping_genome_bam_temp,
                                                 mapping_genome_bam),
           container=pm.container)

    if args.complexity:
        pm.run([cmd_dups, cmd2_dups], mapping_genome_bam_dups,
               container=pm.container)

    pm.timestamp("### Compress all unmapped read files")
    for unmapped_fq in to_compress:
        # Compress unmapped fastq reads
        if not pypiper.is_gzipped_fastq(unmapped_fq) and not unmapped_fq == '':
            cmd = (ngstk.ziptool + " " + unmapped_fq)
            unmapped_fq = unmapped_fq + ".gz"
            pm.run(cmd, unmapped_fq)

    temp_mapping_index = os.path.join(mapping_genome_bam_temp + ".bai")
    temp_mapping_index_dups = os.path.join(mapping_genome_bam_temp_dups + ".bai")

    if not args.prealignments and os.path.exists(mapping_genome_bam_temp):
        # Index the temporary bam file
        cmd = tools.samtools + " index " + mapping_genome_bam_temp
        pm.run(cmd, temp_mapping_index)
        pm.clean_add(temp_mapping_index)

        if args.complexity:
            cmd_dups = tools.samtools + " index " + mapping_genome_bam_temp_dups
            pm.run(cmd_dups, temp_mapping_index_dups)
            pm.clean_add(temp_mapping_index_dups)
            pm.clean_add(mapping_genome_bam_temp_dups)

    # Determine mitochondrial read counts
    # TODO: instead do this to rDNA?
    mito_name = ["chrM", "chrMT", "M", "MT", "rCRSd", "rCRSd_3k"]
    if os.path.exists(mapping_genome_bam_temp):
        if not os.path.exists(temp_mapping_index):
            cmd = tools.samtools + " index " + mapping_genome_bam_temp
            pm.run(cmd, temp_mapping_index)
            pm.clean_add(temp_mapping_index)

        cmd = (tools.samtools + " idxstats " + mapping_genome_bam_temp +
               " | grep")
        for name in mito_name:
            cmd += " -we '" + name + "'"
        cmd += "| cut -f 3"
        mr = pm.checkprint(cmd)
    
        # If there are mitochondrial reads, report and remove them
        if mr and mr.strip():
            pm.report_result("Mitochondrial_reads", round(float(mr)))
            # Index the sort'ed BAM file first
            mapping_genome_index = os.path.join(mapping_genome_bam + ".bai")
            noMT_mapping_genome_bam = os.path.join(
                map_genome_folder, args.sample_name + "_noMT.bam")

            cmd1 = tools.samtools + " index " + mapping_genome_bam
            cmd2 = (tools.samtools + " idxstats " + mapping_genome_bam +
                    " | cut -f 1 | grep")
            for name in mito_name:
                cmd2 += " -vwe '" + name + "'"
            cmd2 += ("| xargs " + tools.samtools + " view -b -@ " +
                     str(pm.cores) + " " + mapping_genome_bam + " > " +
                     noMT_mapping_genome_bam)
            cmd3 = ("mv " + noMT_mapping_genome_bam + " " + mapping_genome_bam)
            cmd4 = tools.samtools + " index " + mapping_genome_bam
            pm.run([cmd1, cmd2, cmd3, cmd4], noMT_mapping_genome_bam)
            pm.clean_add(mapping_genome_index)

    # Determine maximum read length
    cmd = (tools.samtools + " stats " + mapping_genome_bam +
           " | grep '^SN' | cut -f 2- | grep 'maximum length:' | cut -f 2-")
    max_len = int(pm.checkprint(cmd))

    if args.max_len != -1:
        max_len = args.max_len

    if args.complexity:
        if os.path.exists(mapping_genome_bam_temp_dups):
            if not os.path.exists(temp_mapping_index_dups):
                cmd = tools.samtools + " index " + mapping_genome_bam_temp_dups
                pm.run(cmd, temp_mapping_index_dups)
                pm.clean_add(temp_mapping_index_dups)

            cmd_dups = (tools.samtools + " idxstats " +
                        mapping_genome_bam_temp_dups + " | grep")
            for name in mito_name:
                cmd_dups += " -we '" + name + "'"
            cmd_dups += "| cut -f 3"
            mr_dups = pm.checkprint(cmd_dups)

            if mr_dups and mr_dups.strip():
                # Index the sort'ed BAM file first
                mapping_genome_index_dups = os.path.join(
                    mapping_genome_bam_dups + ".bai")
                noMT_mapping_genome_bam_dups = os.path.join(
                    map_genome_folder, args.sample_name + "_noMT_dups.bam")

                cmd1 = tools.samtools + " index " + mapping_genome_bam_dups
                cmd2 = (tools.samtools + " idxstats " +
                        mapping_genome_bam_dups + " | cut -f 1 | grep")
                for name in mito_name:
                    cmd2 += " -vwe '" + name + "'"
                cmd2 += ("| xargs " + tools.samtools + " view -b -@ " +
                         str(pm.cores) + " " + mapping_genome_bam_dups +
                         " > " + noMT_mapping_genome_bam_dups)
                cmd3 = ("mv " + noMT_mapping_genome_bam_dups + " " +
                        mapping_genome_bam_dups)
                cmd4 = tools.samtools + " index " + mapping_genome_bam_dups
                pm.run([cmd1, cmd2, cmd3, cmd4], noMT_mapping_genome_bam_dups)
                pm.clean_add(mapping_genome_index_dups)

        # Calculate library complexity
        pm.timestamp("### Calculate library complexity")
        QC_folder = os.path.join(param.outfolder, "QC_" + args.genome_assembly)
        ngstk.make_dir(QC_folder)

        preseq_output = os.path.join(
            QC_folder, args.sample_name + "_preseq_out.txt")
        preseq_yield = os.path.join(
            QC_folder, args.sample_name + "_preseq_yield.txt")
        preseq_mr = os.path.join(
            QC_folder, args.sample_name + "_preseq.mr")
        preseq_cov = os.path.join(
            QC_folder, args.sample_name + "_preseq_coverage.txt")
        preseq_counts = os.path.join(
            QC_folder, args.sample_name + "_preseq_counts.txt")
        preseq_plot = os.path.join(
            QC_folder, args.sample_name + "_preseq_plot")
        preseq_pdf = os.path.join(
            QC_folder, args.sample_name + "_preseq_plot.pdf")
        preseq_png = os.path.join(
            QC_folder, args.sample_name + "_preseq_plot.png")

        cmd1 = (tools.preseq + " c_curve -v -o " + preseq_output +
                " -B " + mapping_genome_bam_dups)
        pm.run(cmd1, preseq_output)

        cmd2 = (tools.preseq + " lc_extrap -v -o " + preseq_yield +
                " -B " + mapping_genome_bam_dups)
        pm.run(cmd2, preseq_yield, nofail=True)

        if os.path.exists(preseq_yield):
            cmd3 = ("bam2mr " + mapping_genome_bam_dups +
                    " > " + preseq_mr)
            cmd4 = (tools.preseq + " gc_extrap -v -o " + preseq_cov +
                    " " + preseq_mr)
            cmd5 = ("echo '" + preseq_yield +
                    " '$(" + tools.samtools + " view -c -F 4 " + 
                    mapping_genome_bam_dups + ")" + "' '" +
                    "$(" + tools.samtools + " view -c -F 4 " +
                    mapping_genome_bam + ") > " + preseq_counts)

            pm.run([cmd3, cmd4, cmd5],
                   [preseq_mr, preseq_cov, preseq_counts])
            pm.clean_add(preseq_mr)
            pm.clean_add(mapping_genome_bam_dups)
            pm.clean_add(mapping_genome_bam_temp_dups)

            cmd = ("awk '{sum+=$2} END {printf \"%.0f\", sum}' " + res.chrom_sizes)
            genome_size = int(pm.checkprint(cmd))

            cmd = (tools.Rscript + " " + tool_path("PEPPRO.R") +
                   " preseq " + "-i " + preseq_yield)
            if args.coverage:
                cmd += (" -c " + str(genome_size) + " -l " + max_len)
            cmd += (" -r " + preseq_counts + " -o " + preseq_plot)

            pm.run(cmd, [preseq_pdf, preseq_png])

            pm.report_object("Library complexity", preseq_pdf,
                             anchor_image=preseq_png)
        else:
            print("Unable to calculate library complexity.")

    # Calculate quality control metrics for the alignment file
    pm.timestamp("### Calculate NRF, PBC1, and PBC2")

    # Need index for mapping_genome_bam before calculating bamQC metrics
    mapping_genome_index = os.path.join(mapping_genome_bam + ".bai")
    cmd = tools.samtools + " index " + mapping_genome_bam
    pm.run(cmd, mapping_genome_index)

    bamQC = os.path.join(QC_folder, args.sample_name + "_bamQC.tsv")
    cmd = tool_path("bamQC.py")
    cmd += " -i " + mapping_genome_bam
    cmd += " -c " + str(pm.cores)
    cmd += " -o " + bamQC

    def report_bam_qc(bamqc_log):
        # Reported BAM QC metrics via the bamQC metrics file
        if os.path.isfile(bamqc_log):
            cmd1 = ("awk '{ for (i=1; i<=NF; ++i) {" +
                    " if ($i ~ \"NRF\") c=i } getline; print $c }' " +
                    bamqc_log)
            cmd2 = ("awk '{ for (i=1; i<=NF; ++i) {" +
                    " if ($i ~ \"PBC1\") c=i } getline; print $c }' " +
                    bamqc_log)
            cmd3 = ("awk '{ for (i=1; i<=NF; ++i) {" +
                    " if ($i ~ \"PBC2\") c=i } getline; print $c }' " +
                    bamqc_log)
            nrf = pm.checkprint(cmd1)
            pbc1 = pm.checkprint(cmd2)
            pbc2 = pm.checkprint(cmd3)
        else:
            # there were no successful chromosomes yielding results
            nrf = 0
            pbc1 = 0
            pbc2 = 0

        pm.report_result("NRF", round(float(nrf),2))
        pm.report_result("PBC1", round(float(pbc1),2))
        pm.report_result("PBC2", round(float(pbc2), 2))

    pm.run(cmd, bamQC, follow=lambda: report_bam_qc(bamQC),
           container=pm.container)

    # Now produce the unmapped file
    def count_unmapped_reads():
        # Report total number of unmapped reads (-f 4)
        cmd = (tools.samtools + " view -c -f 4 -@ " + str(pm.cores) +
               " " + mapping_genome_bam_temp)
        ur = pm.checkprint(cmd)
        pm.report_result("Unmapped_reads", round(float(ur)))

    unmap_cmd = tools.samtools + " view -b -@ " + str(pm.cores)
    if args.paired_end:
        # require both read and mate unmapped
        unmap_cmd += " -f 12 "
    else:
        # require only read unmapped
        unmap_cmd += " -f 4 "

    unmap_cmd += " " + mapping_genome_bam_temp + " > " + unmap_genome_bam
    pm.run(unmap_cmd, unmap_genome_bam, follow=count_unmapped_reads,
           container=pm.container)

    # Remove temporary bam file from unmapped file production
    pm.clean_add(mapping_genome_bam_temp)

    # Separate by strand
    pm.timestamp("### Split BAM by strand")
    plus_bam = os.path.join(
        map_genome_folder, args.sample_name + "_plus.bam")
    minus_bam = os.path.join(
        map_genome_folder, args.sample_name + "_minus.bam")
    
    cmd1 = build_command([
        tools.samtools,
        "view",
        "-bh",
        ("-F", 20),
        mapping_genome_bam,
        (">", plus_bam)
    ])
    cmd2 = build_command([
        tools.samtools,
        "view",
        "-bh",
        ("-f", 0x10),
        mapping_genome_bam,
        (">", minus_bam)
    ])
    
    pm.run([cmd1,cmd2], minus_bam)

    # TSS enrichment
    if not os.path.exists(res.tss_annotation):
        print("Skipping TSS -- TSS enrichment requires TSS annotation file: {}"
              .format(res.tss_annotation))
    else:
        pm.timestamp("### Calculate TSS enrichment")

        # Split TSS file
        plus_TSS  = os.path.join(QC_folder, "plus_TSS.tsv")
        minus_TSS = os.path.join(QC_folder, "minus_TSS.tsv")
        cmd = ("sed -n -e '/[[:space:]]+[[:space:]]/w " +
               plus_TSS + "' -e '/[[:space:]]-[[:space:]]/w " +
               minus_TSS + "' " + res.tss_annotation)
        pm.run(cmd, [plus_TSS, minus_TSS])

        # pyTssEnrichment requires indexed bam
        if not os.path.exists(mapping_genome_index):
            cmd = build_command([
                tools.samtools,
                "index",
                mapping_genome_bam
            ])
            pm.run(cmd, mapping_genome_index)

        # Plus TSS enrichment
        Tss_plus = os.path.join(QC_folder, args.sample_name +
                                "_plus_TssEnrichment.txt")
        cmd = tool_path("pyTssEnrichment.py")
        cmd += " -a " + mapping_genome_bam + " -b " + plus_TSS + " -p ends"
        cmd += " -c " + str(pm.cores)
        cmd += " -z -v -s 4 -o " + Tss_plus
        pm.run(cmd, Tss_plus, nofail=True)
        pm.clean_add(plus_TSS)
        pm.clean_add(Tss_plus)

        with open(Tss_plus) as f:
            floats = list(map(float, f))
        try:
            # If the TSS enrichment is 0, don't report            
            Tss_score = (
                (sum(floats[int(floats.index(max(floats))-49):
                            int(floats.index(max(floats))+51)]) / 100) /
                (sum(floats[1:int(len(floats)*0.05)]) / int(len(floats)*0.05)))
            pm.report_result("TSS_Plus_Score", round(Tss_score, 1))
        except ZeroDivisionError:
            pass

        # Minus TSS enrichment
        Tss_minus = os.path.join(QC_folder, args.sample_name +
                                 "_minus_TssEnrichment.txt")
        cmd = tool_path("pyTssEnrichment.py")
        cmd += " -a " + mapping_genome_bam + " -b " + minus_TSS + " -p ends"
        cmd += " -c " + str(pm.cores)
        cmd += " -z -v -s 4 -o " + Tss_minus
        pm.run(cmd, Tss_minus, nofail=True)
        pm.clean_add(minus_TSS)
        pm.clean_add(Tss_minus)

        with open(Tss_minus) as f:
            floats = list(map(float, f))
        try:
            # If the TSS enrichment is 0, don't report
            Tss_score = (
                (sum(floats[int(floats.index(max(floats))-49):
                            int(floats.index(max(floats))+51)]) / 100) /
                (sum(floats[1:int(len(floats)*0.05)]) / int(len(floats)*0.05)))
            pm.report_result("TSS_Minus_Score", round(Tss_score, 1))
        except ZeroDivisionError:
            pass

        # Call Rscript to plot TSS Enrichment
        TSS_pdf = os.path.join(QC_folder,  args.sample_name +
                               "_TSSenrichment.pdf")
        cmd = (tools.Rscript + " " + tool_path("PEPPRO.R"))
        cmd += " tss -i " + Tss_plus + " " + Tss_minus
        pm.run(cmd, TSS_pdf, nofail=True)

        TSS_png = os.path.join(QC_folder,  args.sample_name +
                               "_TSSenrichment.png")
        pm.report_object("TSS enrichment", TSS_pdf, anchor_image=TSS_png)

    # Pause index
    chr_order = os.path.join(QC_folder, "chr_order.txt")
    chr_keep = os.path.join(QC_folder, "chr_keep.txt")

    if not os.path.exists(chr_order):
        cmd = (tools.samtools + " view -H " + mapping_genome_bam +
               " | grep 'SN:' | awk -F':' '{print $2,$3}' | " +
               "awk -F' ' -v OFS='\t' '{print $1,$3}' > " + chr_order)
        pm.run(cmd, chr_order)
        pm.clean_add(chr_order)

    if not os.path.exists(chr_keep):
        if os.path.exists(chr_order):
            cmd = ("cut -f 1 " + chr_order + " > " + chr_keep)
            pm.run(cmd, chr_keep)
            pm.clean_add(chr_keep)
        else:
            cmd1 = (tools.samtools + " view -H " + mapping_genome_bam +
                    " | grep 'SN:' | awk -F':' '{print $2,$3}' | " +
                    "awk -F' ' -v OFS='\t' '{print $1,$3}' > " + chr_order)
            cmd2 = ("cut -f 1 " + chr_order + " > " + chr_keep)
            pm.run([cmd1, cmd2], [chr_order, chr_keep])
            pm.clean_add(chr_order)
            pm.clean_add(chr_keep)

    if not os.path.exists(res.pi_annotation):
        print("Skipping PI -- Pause index requires PI annotation file: {}"
              .format(res.pi_annotation))
    else:
        pm.timestamp("### Calculate Pause Index (PI)")

        # Remove missing chr from PI annotation
        pi_local = os.path.join(QC_folder, args.genome_assembly + "_PI.tsv")
        cmd = ("grep -wf " + chr_keep + " " + res.pi_annotation +
                " | awk '$2>0 && $3>0' | awk '$8>0 && $9>0'" +
                " > " + pi_local)
        pm.run(cmd, pi_local, nofail=True)
        pm.clean_add(pi_local)

        # Split PI annotation file into TSS and Gene Body windows
        tss_window = os.path.join(QC_folder, args.genome_assembly +
                                  "_tss_window.bed")
        gene_body_window = os.path.join(QC_folder, args.genome_assembly +
                                        "_gene_body_window.bed")
        cmd1 = ("cut -f 1-6 " + pi_local + " | " + tools.bedtools +
                " sort -i stdin -faidx " + chr_order + " > " + tss_window)
        cmd2 = ("cut -f 7-12 " + pi_local + " | " + tools.bedtools +
                " sort -i stdin -faidx " + chr_order + " > " + gene_body_window)
        pm.run([cmd1, cmd2], [tss_window, gene_body_window])
        pm.clean_add(tss_window)
        pm.clean_add(gene_body_window)

        TSS_density = os.path.join(QC_folder, args.sample_name +
                                   "_TSS_density.bed")
        cmd = (tools.bedtools + " coverage -sorted -counts -s -a " +
               tss_window + " -b " + mapping_genome_bam +
               " -g " + chr_order + " > " + TSS_density)
        pm.run(cmd, TSS_density, nofail=True)
        pm.clean_add(TSS_density)

        gene_body_density = os.path.join(QC_folder, args.sample_name +
                                         "_gene_body_density.bed")
        cmd = (tools.bedtools + " coverage -sorted -counts -s -a " +
               gene_body_window + " -b " + mapping_genome_bam +
               " -g " + chr_order + " > " + gene_body_density)
        pm.run(cmd, gene_body_density, nofail=True)
        pm.clean_add(gene_body_density)

        # Number of reads / window length
        cmd = ("awk '$7>0' " + TSS_density +
               " | awk '{sum+=$7/($3-$2);} END {print sum;}'")
        val = pm.checkprint(cmd)
        if val and val.strip():
            tss_val = float(val)

        cmd = ("awk '$7>0' " + gene_body_density +
               " | awk '{sum+=$7/($3-$2);} END {print sum;}'")
        val = pm.checkprint(cmd)
        if val and val.strip():
            gene_body_val = float(val)
        
        try:
            if tss_val and gene_body_val:
                pi = float(tss_val/gene_body_val)
                pm.report_result("Pause index", round(pi, 2))
        except ZeroDivisionError:
            pass

    # Fraction of reads in Pre-mRNA (FRiP)
    if not os.path.exists(res.pre_mRNA_annotation):
        print("Skipping FRiP -- Fraction of reads in pre-mRNA requires "
              "pre-mRNA annotation file: {}"
              .format(res.pre_mRNA_annotation))
    else:
        pm.timestamp("### Calculate FRiP")
        # Plus
        plus_frip = calc_frip(plus_bam, res.pre_mRNA_annotation,
                              frip_func=ngstk.simple_frip,
                              pipeline_manager=pm)
        pm.report_result("Plus FRiP", round(plus_frip, 2))
        # Minus
        minus_frip = calc_frip(minus_bam, res.pre_mRNA_annotation,
                               frip_func=ngstk.simple_frip,
                               pipeline_manager=pm)
        pm.report_result("Minus FRiP", round(minus_frip, 2))

    # Fragment distribution
    if args.paired_end:
        pm.timestamp("### Plot fragment distribution")
        frag_len = os.path.join(QC_folder, args.sample_name + "_fragLen.txt")
        frag_dist_tool = tool_path("fragment_length_dist.pl")
        cmd = build_command([tools.perl, frag_dist_tool,
                             mapping_genome_bam, frag_len])

        fragL_counts_file = args.sample_name + "_fragCount.txt"
        fragL_count = os.path.join(QC_folder, fragL_counts_file)
        cmd1 = "sort -n  " + frag_len + " | uniq -c  > " + fragL_count

        fragL_dis1 = os.path.join(QC_folder, args.sample_name +
                                  "_fragLenDistribution.pdf")
        fragL_dis2 = os.path.join(QC_folder, args.sample_name +
                                  "_fragLenDistribution.txt")

        cmd2 = (tools.Rscript + " " + tool_path("PEPPRO.R"))
        cmd2 += " frag -l " + frag_len + " -c " + fragL_count
        cmd2 += " -p " + fragL_dis1 + " -t " + fragL_dis2

        pm.run([cmd, cmd1, cmd2], fragL_dis1, nofail=True,
               container=pm.container)
        pm.clean_add(frag_len)
        pm.clean_add(fragL_count)

        fragL_png = os.path.join(QC_folder, args.sample_name +
                                 "_fragLenDistribution.png")
        pm.report_object("Fragment distribution", fragL_dis1,
                         anchor_image=fragL_png)

    # Calculate fraction of reads in features of interest
    pm.timestamp("### Calculate fraction of reads in features (FRiF)")

    # Custom annotation file or direct path to annotation file is specified
    anno_local = ''

    if os.path.exists(res.feat_annotation):
        if res.feat_annotation.endswith(".gz"):
            anno_local = os.path.join(raw_folder,
                                      args.genome_assembly +
                                      "_annotations.bed.gz")
            cmd = ("ln -sf " + res.feat_annotation + " " + anno_local)
            pm.run(cmd, anno_local)
        elif res.feat_annotation.endswith(".bed"):
            anno_local = os.path.join(raw_folder,
                                      args.genome_assembly +
                                      "_annotations.bed")
            cmd = ("ln -sf " + res.feat_annotation + " " + anno_local)
            pm.run(cmd, anno_local)
        else:
            print("Skipping read annotation...")
            print("This requires a {} annotation file."
                  .format(args.genome_assembly))
            print("Could not find {}.`"
                  .format(str(os.path.dirname(res.feat_annotation))))

    anno_list_plus = list()
    anno_list_minus = list()

    if os.path.isfile(anno_local):
        # Get list of features
        cmd1 = (ngstk.ziptool + " -d -c " + anno_local +
                " | cut -f 4 | sort -u")
        ft_list = pm.checkprint(cmd1, shell=True)
        ft_list = ft_list.splitlines()

        # Split annotation file on features
        cmd2 = (ngstk.ziptool + " -d -c " + anno_local +
                " | awk -F'\t' '{print>\"" + QC_folder + "/\"$4}'")
        if len(ft_list) >= 1:
            for pos, anno in enumerate(ft_list):
                # working files
                anno_file = os.path.join(QC_folder, anno)
                valid_name = re.sub('[^\w_.)( -]', '', anno).strip().replace(' ', '_')
                file_name = os.path.join(QC_folder, valid_name)
                anno_sort = os.path.join(QC_folder, valid_name + "_sort.bed")
                anno_cov_plus = os.path.join(QC_folder,
                                             args.sample_name + "_" +
                                             valid_name + "_plus_coverage.bed")
                anno_cov_minus = os.path.join(QC_folder,
                                              args.sample_name + "_" +
                                              valid_name +
                                              "_minus_coverage.bed")

                # Extract feature files
                pm.run(cmd2, anno_file)

                # Rename files to valid file_names
                # Avoid 'mv' "are the same file" error
                if not os.path.exists(file_name):
                    cmd = 'mv "{old}" "{new}"'.format(old=anno_file,
                                                      new=file_name)
                    pm.run(cmd, file_name)

                # Sort files
                cmd3 = ("cut -f 1-3 " + file_name +
                        " | bedtools sort -i stdin -faidx " +
                        chr_order + " > " + anno_sort)
                pm.run(cmd3, anno_sort)
                
                anno_list_plus.append(anno_cov_plus)
                anno_list_minus.append(anno_cov_minus)
                cmd4 = (tools.bedtools + " coverage -sorted -counts -a " +
                        anno_sort + " -b " + plus_bam +
                        " -g " + chr_order + " > " +
                        anno_cov_plus)
                cmd5 = (tools.bedtools + " coverage -sorted -counts -a " +
                        anno_sort + " -b " + minus_bam +
                        " -g " + chr_order + " > " +
                        anno_cov_minus)
                pm.run(cmd4, anno_cov_plus, 
                       container=pm.container)
                pm.run(cmd5, anno_cov_minus,
                       container=pm.container)
                pm.clean_add(file_name)
                pm.clean_add(anno_sort)
                pm.clean_add(anno_cov_plus)
                pm.clean_add(anno_cov_minus)

    # Report mRNA contamination
    if (os.path.exists(res.exon_annotation) and
        os.path.exists(res.intron_annotation)):

        pm.timestamp("### Calculate mRNA contamination")

        # Sort exons and introns
        exons_sort = os.path.join(QC_folder, args.genome_assembly +
                                  "_exons_sort.bed")
        introns_sort = os.path.join(QC_folder, args.genome_assembly +
                                    "_introns_sort.bed")
        cmd1 = ("grep -wf " + chr_keep + " " + res.exon_annotation +
                " | " + tools.bedtools + " sort -i stdin -faidx " +
                chr_order + " > " + exons_sort)
        # a single sort fails to sort a 1 bp different start position intron
        cmd2 = ("grep -wf " + chr_keep + " " + res.intron_annotation +
                " | " + tools.bedtools + " sort -i stdin -faidx " +
                chr_order + " | " + tools.bedtools + " sort -i stdin -faidx " +
                chr_order + " > " + introns_sort)
        pm.run([cmd1, cmd2], [exons_sort, introns_sort], nofail=True)
        pm.clean_add(exons_sort)
        pm.clean_add(introns_sort)
        
        # Determine coverage of exons/introns
        exons_cov = os.path.join(QC_folder, args.sample_name +
                                 "_exons_coverage.bed")
        introns_cov = os.path.join(QC_folder, args.sample_name +
                                   "_introns_coverage.bed")
        cmd1 = (tools.bedtools + " coverage -sorted -counts -s -a " +
                exons_sort + " -b " + mapping_genome_bam +
                " -g " + chr_order + " > " + exons_cov)
        cmd2 = (tools.bedtools + " coverage -sorted -counts -s -a " +
                introns_sort + " -b " + mapping_genome_bam +
                " -g " + chr_order + " > " + introns_cov)
        pm.run([cmd1, cmd2], [exons_cov, introns_cov], nofail=True)
        pm.clean_add(exons_cov)
        pm.clean_add(introns_cov)

        # need Total Reads divided by 1M
        ar = float(pm.get_stat("Aligned_reads"))
        scaling_factor = float(ar/1000000)

        exons_rpkm = os.path.join(QC_folder, args.sample_name +
                                  "_exons_rpkm.tsv")
        introns_rpkm = os.path.join(QC_folder, args.sample_name +
                                    "_introns_rpkm.tsv")

        # determine exonic RPKM for individual genes
        if os.path.exists(exons_cov):
            cmd = ("awk -v OFS='\t' '{readCount[$4] += $7; " + 
                   "exonCount[$4] += 1; geneSizeKB[$4] += " +
                   "(sqrt(($3-$2+0.00000001)^2)/1000); " +
                   "gene[$4] = $4} END { for (a in readCount) " +
                   "{ print gene[a], (readCount[a]/" +
                    str(scaling_factor) + ")/geneSizeKB[a]}}' " + exons_cov +
                    " | awk '$2>0' | sort -k1 > " + exons_rpkm)
            pm.run(cmd, exons_rpkm, nofail=True)
            pm.clean_add(exons_rpkm)

        # determine intronic RPKM for individual genes
        if os.path.exists(introns_cov):
            cmd = ("awk -v OFS='\t' '{readCount[$4] += $7; " + 
                   "exonCount[$4] += 1; geneSizeKB[$4] += " +
                   "(sqrt(($3-$2+0.00000001)^2)/1000); " +
                   "gene[$4] = $4} END { for (a in readCount) " +
                   "{ print gene[a], (readCount[a]/" +
                    str(scaling_factor) + ")/geneSizeKB[a]}}' " + introns_cov +
                    " | awk '$2>0' | sort -k1 > " + introns_rpkm)
            pm.run(cmd, introns_rpkm, nofail=True)
            pm.clean_add(introns_rpkm)

        # join intron, exon RPKM on gene name
        intron_exon = os.path.join(QC_folder, args.sample_name +
                                   "_intron_exon.tsv")
        if os.path.exists(exons_rpkm) and os.path.exists(introns_rpkm):
            cmd = ("join -a1 -a2 -j1 -e0 -o 0 1.2 2.2 " +
                   introns_rpkm + " " + exons_rpkm + " > " + intron_exon)
            pm.run(cmd, intron_exon, nofail=True)
            pm.clean_add(intron_exon)

        # compare intron to exon RPKM and report result
        if os.path.exists(intron_exon):
            cmd = ("awk '$2>0' " + intron_exon +
                   " | awk '{print $3/$2}' | sort -n | awk ' { a[i++]=$1; }" +
                   " END { x=int((i+1)/2);" +
                   " if (x < (i+1)/2) print (a[x-1]+a[x])/2;" +
                   " else print a[x-1]; }'")
            mrna_con = float(pm.checkprint(cmd))
            pm.report_result("mRNA contamination", round(mrna_con, 2))

        # plot mRNA contamination distribution
        mRNApdf = os.path.join(QC_folder,
            args.sample_name + "_mRNA_contamination.pdf")
        mRNApng = os.path.join(QC_folder,
            args.sample_name + "_mRNA_contamination.png")
        mRNAplot = [tools.Rscript, tool_path("PEPPRO.R"), "mrna",
                    "-i", intron_exon, "--raw"]
        cmd = build_command(mRNAplot)
        pm.run(cmd, mRNApdf, nofail=False)
        pm.report_object("mRNA contamination", mRNApdf, anchor_image=mRNApng)

    # Plot FRiF
    pm.timestamp("### Plot FRiF")
    # Plus
    cmd = (tools.samtools + " view -@ " + str(pm.cores) + " " +
           param.samtools.params + " -c -F4 " + plus_bam)
    totalReads = pm.checkprint(cmd)
    totalReads = str(totalReads).rstrip()

    frifPDF = os.path.join(QC_folder, args.sample_name + "_plus_frif.pdf")
    frifPNG = os.path.join(QC_folder, args.sample_name + "_plus_frif.png")
    frifCmd = [tools.Rscript, tool_path("PEPPRO.R"), "frif",
               "-n", args.sample_name, "-r", totalReads,
               "-o", frifPDF, "--bed"]
    for cov in anno_list_plus:
        frifCmd.append(cov)
    cmd = build_command(frifCmd)
    pm.run(cmd, frifPDF, nofail=False)
    pm.report_object("Plus FRiF", frifPDF, anchor_image=frifPNG)

    # Minus
    cmd = (tools.samtools + " view -@ " + str(pm.cores) + " " +
           param.samtools.params + " -c -F4 " + minus_bam)
    totalReads = pm.checkprint(cmd)
    totalReads = str(totalReads).rstrip()

    frifPDF = os.path.join(QC_folder, args.sample_name + "_minus_frif.pdf")
    frifPNG = os.path.join(QC_folder, args.sample_name + "_minus_frif.png")
    frifCmd = [tools.Rscript, tool_path("PEPPRO.R"), "frif",
               "-n", args.sample_name, "-r", totalReads,
               "-o", frifPDF, "--bed"]
    for cov in anno_list_minus:
        frifCmd.append(cov)
    cmd = build_command(frifCmd)
    pm.run(cmd, frifPDF, nofail=False)
    pm.report_object("Minus FRiF", frifPDF, anchor_image=frifPNG)

    # Shift and produce BigWig's
    genome_fq = os.path.join(res.rgc.genome_folder,
                             args.genome_assembly,
                             (args.genome_assembly + ".fa"))
    signal_folder = os.path.join(
        param.outfolder, "signal_" + args.genome_assembly)
    ngstk.make_dir(signal_folder)
    plus_bw = os.path.join(
        signal_folder, args.sample_name + "_plus_body_0-mer.bw")
    minus_bw = os.path.join(
        signal_folder, args.sample_name + "_minus_body_0-mer.bw")
    
    if not args.sob:
        # If not scaling we don't need to use seqOutBias to generate the
        # separate strand bigWigs; just convert the BAM's directly with 
        # bamSitesToWig.py which uses UCSC wigToBigWig
        pm.timestamp("### Produce bigWig files")

        wig_cmd_callable = ngstk.check_command("wigToBigWig")

        if wig_cmd_callable:
            cmd1 = tools.samtools + " index " + plus_bam
            cmd2 = tool_path("bamSitesToWig.py")
            cmd2 += " -i " + plus_bam
            cmd2 += " -c " + res.chrom_sizes
            cmd2 += " -o " + plus_bw  # DEBUG formerly smoothed " -w " + plus_bw
            cmd2 += " -p " + str(int(max(1, int(pm.cores) * 2/3)))
            cmd2 += " --variable-step"
            if args.runon.lower() == "pro":
                cmd2 += " --tail-edge"
            pm.run([cmd1, cmd2], plus_bw)

            cmd3 = tools.samtools + " index " + minus_bam
            cmd4 = tool_path("bamSitesToWig.py")
            cmd4 += " -i " + minus_bam
            cmd4 += " -c " + res.chrom_sizes
            cmd4 += " -o " + minus_bw # DEBUG formerly smoothed " -w " + minus_bw
            cmd4 += " -p " + str(int(max(1, int(pm.cores) * 2/3)))
            cmd4 += " --variable-step"
            if args.runon.lower() == "pro":
                cmd4 += " --tail-edge"
            pm.run([cmd3, cmd4], minus_bw)
        else:
            print("Skipping signal track production -- Could not call \'wigToBigWig\'.")
            print("Check that you have the required UCSC tools in your PATH.")
    else:
        # Need to run seqOutBias tallymer separately
        # Do that in the $GENOMES folder, in a subfolder called "mappability"
        # Only need to do that once for each read-size of interest
        # default would be read-size 30 (args.max_len)
        mappability_folder = os.path.join(res.rgc.genome_folder,
                                          args.genome_assembly,
                                          "mappability")
        ngstk.make_dir(mappability_folder)

        # Link fasta file
        genome_fq_ln = os.path.join(mappability_folder,
                                   (args.genome_assembly + ".fa"))
        if not os.path.isfile(genome_fq_ln):
            cmd = "ln -sf " + genome_fq + " " + genome_fq_ln
            pm.run(cmd, genome_fq_ln)

        if args.max_len != -1:
            max_len = args.max_len

        suffix_index = os.path.join(mappability_folder,
                                    (args.genome_assembly + ".sft"))
        suffix_check = os.path.join(mappability_folder,
                                    (args.genome_assembly + ".sft.suf"))
        tally_index = os.path.join(mappability_folder,
                                   (args.genome_assembly + ".tal_" +
                                   str(max_len)))
        tally_check = os.path.join(mappability_folder,
                                   (args.genome_assembly + ".tal_" + 
                                   str(max_len) + ".mer"))
        search_file = os.path.join(mappability_folder,
                                   (args.genome_assembly + ".tal_" +
                                   str(max_len) + ".gtTxt"))

        map_files = [suffix_check, tally_check, search_file]
        already_mapped = False
        
        for file in map_files:
            if os.path.isfile(file) and os.stat(file).st_size > 0:
                already_mapped = True
            else:
                already_mapped = False
        
        if not already_mapped:
            pm.timestamp("### Compute mappability information")
        
            suffix_cmd_chunks = [
                ("gt", "suffixerator"),
                "-dna",
                "-pl",
                "-tis",
                "-suf",
                "-lcp",
                "-v",
                ("-parts", args.parts),
                ("-db", genome_fq_ln),
                ("-indexname", suffix_index)
            ]
            suffix_cmd = build_command(suffix_cmd_chunks)
            pm.run(suffix_cmd, suffix_index)

            tally_cmd_chunks = [
                ("gt", "tallymer"),
                "mkindex",
                ("-mersize", max_len),
                ("-minocc", 2),
                ("-indexname", tally_index),
                "-counts",
                "-pl",
                ("-esa", suffix_index)
            ]
            tally_cmd = build_command(tally_cmd_chunks)
            pm.run(tally_cmd, tally_index)

            search_cmd_chunks = [
                ("gt", "tallymer"),
                "search",
                "-output", 
                ("qseqnum", "qpos"),
                ("-strand", "fp"),
                ("-tyr", tally_index),
                ("-q", genome_fq_ln),
                (">", search_file)
            ]
            search_cmd = build_command(search_cmd_chunks)
            pm.run(search_cmd, search_file)

        pm.timestamp("### Use seqOutBias to produce bigWig files")

        seqtable = os.path.join(res.genomes, args.genome_assembly,
            mappability_folder, (args.genome_assembly + ".tbl"))

        seqtable_cmd = build_command([
            (tools.seqoutbias, "seqtable"),
            genome_fq_ln,
            str("--tallymer=" + search_file),
            str("--gt-workdir=" + mappability_folder),  # TODO
            str("--read-size=" + max_len),
            str("--out=" + seqtable)
        ])

        pm.run(seqtable_cmd, seqtable)

        plus_table = os.path.join(
            signal_folder, (args.genome_assembly + "_plus_tbl.txt"))

        table_plus_cmd = build_command([
            (tools.seqoutbias, "table"),
            seqtable,
            plus_bam,
            (">", plus_table)
        ])

        minus_table = os.path.join(
            signal_folder, (args.genome_assembly + "_minus_tbl.txt"))

        table_minus_cmd = build_command([
            (tools.seqoutbias, "table"),
            seqtable,
            minus_bam,
            (">", minus_table)
        ])

        pm.run([table_plus_cmd, table_minus_cmd], minus_table)

        if args.scale:
            scale_plus_chunks = [
                (tools.seqoutbias, "scale"),
                seqtable,
                plus_bam,
                "--skip-bed",
                str("--bw=" + plus_bw)
            ]
            if args.runon.lower() == "pro":
                scale_plus_chunks.extend([("--tail-edge")])
            scale_plus_cmd = build_command(scale_plus_chunks)

            scale_minus_chunks = [
                (tools.seqoutbias, "scale"),
                seqtable,
                minus_bam,
                "--skip-bed",
                str("--bw=" + minus_bw),
            ]
            if args.runon.lower() == "pro":
                scale_minus_chunks.extend([("--tail-edge")])
            scale_minus_cmd = build_command(scale_minus_chunks)
        else:
            scale_plus_chunks = [
                (tools.seqoutbias, "scale"),
                seqtable,
                plus_bam,
                "--no-scale",
                "--skip-bed",
                str("--bw=" + plus_bw)
            ]
            if args.runon.lower() == "pro":
                scale_plus_chunks.extend([("--tail-edge")])
            scale_plus_cmd = build_command(scale_plus_chunks)

            scale_minus_chunks = [
                (tools.seqoutbias, "scale"),
                seqtable,
                minus_bam,
                "--no-scale",
                "--skip-bed",
                str("--bw=" + minus_bw),
            ]
            if args.runon.lower() == "pro":
                scale_minus_chunks.extend([("--tail-edge")])
            scale_minus_cmd = build_command(scale_minus_chunks)

        pm.run([scale_plus_cmd, scale_minus_cmd], minus_bw)

    # COMPLETE!
    pm.stop_pipeline()


if __name__ == '__main__':
    pm = None
    ngstk = None
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Pipeline aborted.")
        sys.exit(1)
