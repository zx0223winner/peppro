# Getting started

PEPPRO uses a series of publicly-available, common bioinformatics tools including:

* [samtools](http://www.htslib.org/)
* [bowtie2](http://bowtie-bio.sourceforge.net/bowtie2/index.shtml)
* [seqkit](https://bioinf.shenwei.me/seqkit/)
* [fastp](https://github.com/OpenGene/fastp)
* [seqtk](https://github.com/lh3/seqtk)
* [seqOutBias](https://github.com/guertinlab/seqOutBias)
  
## Optional software

Optionally, `PEPPRO` can mix and match tools for adapter removal, read trimming, deduplication, and reverse complementation.  The use of `fqdedup`, in particular, is useful if you wish to minimize memory use at the expense of speed.  I also suggest using the default required tools simply due to the fact that the `fastx toolkit` has not been supported since 2012 and issues with reads utilizing newer Phred quality scores can cause problems.

*Optional tools:*

* [fqdedup](https://github.com/guertinlab/fqdedup)
* [cutadapt](https://cutadapt.readthedocs.io/)
* [fastx toolkit](http://hannonlab.cshl.edu/fastx_toolkit/)

## Reference genomes

The pipeline relies on [`Refgenie` assemblies](https://github.com/databio/refgenie) for alignment.  You have two options for using `refgenie` assemblies. If you're using a common genome, there's a good chance there's already a [downloadable pre-built `refgenie` assembly](http://big.databio.org/refgenomes) for your genome. Otherwise, you can follow the [`refgenie` instructions to create your own](https://github.com/databio/refgenie).

The pipeline looks for genomes stored in a folder specified by the `resources.genomes` attribute in the [pipeline config file](https://github.com/databio/peppro/blob/master/pipelines/peppro.yaml). By default, this points to the shell variable `GENOMES`, so all you have to do is set an environment variable to the location of your refgenie genomes:
```
export GENOMES="/path/to/genomes/folder/"
```
(Add this to your .bashrc or .profile to ensure it persists). Alternatively, you can skip the `GENOMES` variable and simply change the value of the r`resources.genomes` configuration option to point to the folder where you stored the assemblies. 

## Example use

Using the [K562 sample](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM1480327) as our staring point, we can perform FASTQ preparation, alignment, and bigWig production in a single command.  As written, the pipeline looks for the mappability information in a subfolder titled `mappability/` within the parent genome folder.  For example, if I'm using hg38, I'd need a folder like so: `/the/path/to/hg38/mappability/`. To work with this central repository of mappability information, a slightly [modified version of seqOutBias](https://github.com/jpsmith5/seqOutBias/) is required.

You can build that modified version like so (requires Python3 to build):
* clone the repository and move into it
* `cargo build --release`
* copy the `target/release/seqOutBias` file to `/usr/bin` or update your `$PATH` variable to include seqOutBias

### From command line:

We use a shell variable that points to where all processed data should be stored, which we name `PROCESSED`.  You can either define that variable for your environment or just change the example below to the full path you'd like to store the processed output from the pipeline.

```
/pipelines/peppro.py --single-or-paired single 
  --genome hg38 
  --sample-name K562_pro
  --input $DATA/K562_pro.fastq
  --adapter cutadapt
  --dedup fqdedup
  --trimmer fastx
  -O $PROCESSED/pro_example/
```

### Use `Looper`:

If using `looper` and the configuration files provided in the `examples/` folder:

```
looper run examples/K562_example.yaml
```
