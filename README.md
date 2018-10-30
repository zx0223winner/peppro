# PEPPRO

PEPPRO is a pipeline designed to process PRO-seq data. For more information see: http://code.databio.org/PEPPRO/

## Required software

PEPPRO uses a series of publicly-available, common bioinformatics tools including:

* [samtools](http://www.htslib.org/)
* [bowtie2](http://bowtie-bio.sourceforge.net/bowtie2/index.shtml)
* [seqkit](https://bioinf.shenwei.me/seqkit/)
* [fastp](https://github.com/OpenGene/fastp)
* [seqtk](https://github.com/lh3/seqtk)
* [seqOutBias](https://github.com/guertinlab/seqOutBias)
  
## Optional software

Alternatively, `PEPPRO` can mix and match tools for adapter removal, read trimming, deduplication, and reverse complementation.  The use of `fqdedup`, in particular, is useful if you wish to minimize memory use at the expense of increased speed.  I also suggest using the default required tools simply due to the fact that the `fastx toolkit` has not been supported since 2012 and issues with reads utilizing newer Phred quality scores can cause problems.

*Optional tools:*
* [fqdedup](https://github.com/guertinlab/fqdedup)
* [cutadapt](https://cutadapt.readthedocs.io/)
* [fastx toolkit](http://hannonlab.cshl.edu/fastx_toolkit/)

## Example use

Using the [K562 sample](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM1480327) as our staring point, we can perform FASTQ preparation, alignment, and bigWig production in a single command.  As written, the pipeline looks for the mappability information in a subfolder titled `mappability/` within the parent genome folder.  For example, if I'm using hg38, I'd need a folder like so: `/the/path/to/hg38/mappability/`. To work with this central repository of mappability information, a slightly [modified version of seqOutBias](https://github.com/jpsmith5/seqOutBias/) is required.

You can build that modified version like so:
* clone the repository and move into it
* `cargo build --release`
* copy the `target/release/seqOutBias` file to /usr/bin or update your `$PATH` variable to include seqOutBias

For running from the command line:

`/pipelines/peppro.py --single-or-paired single --genome hg38 --sample-name K562_pro --input $DATA/K562_pro.fastq --adapter cutadapt --dedup fqdedup --trimmer fastx -O $PROCESSED/pro_example/`

If using `looper` and the configuration files provided in the `examples/` folder:

`looper run examples/K562_example.yaml`

## Contributing

Pull requests welcome. Active development should occur in a development or feature branch.

## Contributors

* Jason Smith, jasonsmith@virginia.edu
* Mike Guertin, mjg7y@virginia.edu
* Nathan Sheffield, nathan@code.databio.org
* Others... (add your name)
