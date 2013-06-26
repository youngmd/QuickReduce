#! /usr/bin/env python

import sys
import os
import pyfits
import numpy
numpy.seterr(divide='ignore', invalid='ignore')
import scipy
import scipy.stats


import Queue
import threading
import multiprocessing
import ctypes

from podi_definitions import *
import podi_imcombine

avg_sky_countrates = {
    "odi_i": 3.8,
    "odi_z": 4.0,
}

def make_fringing_template(input_filelist, outputfile, return_hdu=False):

    print "Output file=",outputfile

    # First loop over all filenames and make sure all files exist
    hdu_filelist = []
    for file in input_filelist:
        if (os.path.isfile(file)):
            hdu_filelist.append(pyfits.open(file))

    if (len(hdu_filelist) <= 0):
        stdout_write("No existing files found in input list, hence nothing to do!\n")
        return
    
    # Read the input parameters
    # Note that file headers are copied from the first file

    # Create the primary extension of the output file
    ref_hdulist = hdu_filelist[0]
    primhdu = pyfits.PrimaryHDU(header=ref_hdulist[0].header)

    # Add PrimaryHDU to list of OTAs that go into the output file
    out_hdulist = [primhdu]

    #
    # Now loop over all extensions and compute the mean
    #
    for cur_ext in range(1, len(ref_hdulist)):

        data_blocks = []
        # Check what OTA we are dealing with
        if (not is_image_extension(ref_hdulist[cur_ext].header)):
            continue
        extname = ref_hdulist[cur_ext].header['EXTNAME']

        stdout_write("\rCombining frames for OTA %s (#% 2d/% 2d) ..." % (extname, cur_ext, len(ref_hdulist)-1))

        # Now open all the other files, look for the right extension, and copy their image data to buffer
        for file_number in range(0, len(filelist)):

            try:
                this_hdu = hdu_filelist[file_number][extname]
            except:
                continue

            # Skip all OTAs that are marked as video/guide OTAs
            cellmode = this_hdu.header['CELLMODE']
            if (cellmode.find("V") >= 0):
                continue
            
            skylevel = this_hdu.header['SKY_MEDI']
            if ("EXPTIME" in hdu_filelist[file_number][0].header):
                exptime = hdu_filelist[file_number][0].header['EXPTIME']
                filter = hdu_filelist[file_number][0].header['FILTER']
                if (filter in avg_sky_countrates):
                    max_skylevel = 2 * avg_sky_countrates[filter] * exptime
                    if (skylevel > max_skylevel):
                        stdout_write(" (%.1f)" % (skylevel))
                        continue

            fringing = (this_hdu.data - skylevel) / skylevel
            stdout_write(" %.1f" % (skylevel))
            data_blocks.append(fringing)

            # delete the data block to free some memory, since we won't need it anymore
            del this_hdu.data

        stdout_write(" combining ...")
        #combined = podi_imcombine.imcombine_data(data_blocks, "nanmedian")
        combined = podi_imcombine.imcombine_data(data_blocks, "nanmedian.bn")

        # Create new ImageHDU
        # Insert the imcombine'd frame into the output HDU
        # Copy all headers from the reference HDU
        hdu = pyfits.ImageHDU(header=ref_hdulist[cur_ext].header, data=combined)

        # Append the new HDU to the list of result HDUs
        out_hdulist.append(hdu)
        stdout_write(" done!\n")

        del hdu

    return_hdu = False
    out_hdu = pyfits.HDUList(out_hdulist)
    if (not return_hdu and outputfile != None):
        stdout_write(" writing results to file %s ..." % (outputfile))
        clobberfile(outputfile)
        out_hdu.writeto(outputfile, clobber=True)
        out_hdu.close()
        del out_hdu
        del out_hdulist
        stdout_write(" done!\n")
    elif (return_hdu):
        stdout_write(" returning HDU for further processing ...\n")
        return out_hdu
    else:
        stdout_write(" couldn't write output file, no filename given!\n")

    return

if __name__ == "__main__":

    if (cmdline_arg_isset("-singles")):
        for filename in get_clean_cmdline()[1:]:
            outputfile = filename[:-5]+".fringe.fits"
            if (cmdline_arg_isset("-noclobber") and os.path.isfile(outputfile)):
                stdout_write("\n%s already exists, skipping!\n\n" % (outputfile))
                continue

            stdout_write("Converting %s to fringemask ...\n" % (filename))
            hdulist = pyfits.open(filename)

            out_hdu = [pyfits.PrimaryHDU(header=hdulist[0].header)]

            for ext in range(len(hdulist)):
                if (not is_image_extension(hdulist[ext].header)):
                    continue
                
                # Skip all OTAs that are marked as video/guide OTAs
                cellmode = hdulist[ext].header['CELLMODE']
                if (cellmode.find("V") >= 0):
                    continue

                skylevel = hdulist[ext].header['SKY_MEDI']
                fringing = (hdulist[ext].data - skylevel) / skylevel
                stdout_write("   %s = %.1f\n" % (hdulist[ext].header['EXTNAME'], skylevel))

                out_hdu.append(pyfits.ImageHDU(header=hdulist[ext].header,
                                               data=fringing))

            stdout_write("writing (%s)..." % (outputfile))
            out_hdulist = pyfits.HDUList(out_hdu)
            out_hdulist.writeto(outputfile, clobber=True)
            stdout_write(" done!\n\n")

        sys.exit(0)
                               
    
    outputfile = get_clean_cmdline()[1]

    filelist = get_clean_cmdline()[2:]

    operation = cmdline_arg_set_or_default("-op", "mean")

    make_fringing_template(filelist, outputfile, operation)
