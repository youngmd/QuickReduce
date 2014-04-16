#!/usr/bin/env python

import os, sys
d,_=os.path.split(os.path.abspath(sys.argv[0]))
sys.path.append("%s/../"%d)

import podi_asteroids
import podi_swarpstack
from podi_commandline import *
from podi_definitions import *
import podi_logging
import logging
import astropy.io.votable
import math
import numpy
import ephem
import time
import scipy.stats

if __name__ == "__main__":

    
    options = set_default_options()
    podi_logging.setup_logging(options)
    options = read_options_from_commandline(options)

    logger = logging.getLogger("AstroidField")

    params = podi_swarpstack.read_swarp_params()
    inputlist = get_clean_cmdline()[2:]

    target_name = get_clean_cmdline()[1]

    smaller_region = 5 # arcmin on a side

    print params
    print options

    # First, stack all frames without any non-sidereal correction
    outputfile = "%s__reference.fits" % (target_name)

    logger.info("Creating the averaged reference stack")
    returned = podi_swarpstack.swarpstack(outputfile, inputlist, params, options)
    if (returned == None):
        logger.error("something went wrong while creating the reference stack")
        
    else:

        modified_files, single_prepared_files = returned
        print single_prepared_files

        # Open the reference frame
        # This we will need for each single frame
        logger.info("opening reference file")
        hdu_ref = astropy.io.fits.open(outputfile)
        ref_frame = hdu_ref[0].data

        for sglfile in single_prepared_files:

            logger.info("Creating difference image from %s" % (sglfile))
            hdu_sgl = astropy.io.fits.open(sglfile)

            hdu_sgl[0].data -= ref_frame
            
            # Strip the fits from the filename and append a ".diff.fits"
            _, base = os.path.split(sglfile)
            sgl_diff_filename = "%s___%s.diff.fits" % (target_name, base)

            logger.debug("Writing difference image to %s" % (sgl_diff_filename))
            clobberfile(sgl_diff_filename)
            hdu_sgl.writeto(sgl_diff_filename)
            
        logger.info("all done!")


    podi_logging.shutdown_logging(options)