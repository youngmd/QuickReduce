#!/usr/bin/env python

#
# (c) Ralf Kotulla for WIYN/pODI
#

"""
How-to use:

./podi_pipeline2mef.py 


"""

import sys
import os
import pyfits
import numpy
import scipy

gain_correct_frames = False
from podi_definitions import *


do_not_copy_headers = ("NAXIS", "NAXIS1", "NAXIS2")

verbose = False

def mask_broken_regions(datablock, regionfile, verbose=False):

    counter = 0
    file = open(regionfile)
    for line in file:
        if (line[0:3] == "box"):
            coords = line[4:-2]
            coord_list = coords.split(",")
                        
            if (not datablock == None):
                x, y = int(float(coord_list[0])), int(float(coord_list[1]))
                dx, dy = int(0.5*float(coord_list[2])), int(0.5*float(coord_list[3]))
                #mask[y-dy:y+dy,x-dx:x+dx] = 1

                x1 = numpy.max([0, x-dx])
                x2 = numpy.min([datablock.shape[1], x+dx])
                y1 = numpy.max([0, y-dy])
                y2 = numpy.min([datablock.shape[0], y+dy])
                datablock[y1:y2, x1:x2] = numpy.NaN

                # print x,x+dx,y,y+dy
            counter += 1

    file.close()
    if (verbose):
        print "Marked",counter,"bad pixel regions"
    return datablock

if __name__ == "__main__":

    outputfile = sys.argv[1]
    stdout_write("### podi_pipeline2mef\n")

    ota_list = []

    primhdu = pyfits.PrimaryHDU()
    ota_list.append(primhdu)

    for filename in sys.argv[2:]:


        hdulist = pyfits.open(filename)

        #
        # Get some information for the OTA
        #
        fppos = hdulist[0].header['FPPOS']
        filter_name = hdulist[0].header['FILTER']
        exposure_time = hdulist[0].header['EXPTIME']

        stdout_write("\r   Reading file %s: POS=%s, FILTER=%s, Exp-Time=%.1f" % (filename, fppos, filter_name, exposure_time))

	# Create an fits extension
        hdu = pyfits.ImageHDU()

        # Now copy the headers from the original file into the new one
        cards = hdulist[0].header.ascardlist()
        for c in cards:
            try:
                try:
                    # Do NOT copy the NAXIS headers. These are set autmoatically
                    # by pyFITS and otherwise end up at the wrong position
                    idx = do_not_copy_headers.index(c.key)
                    if (verbose):
                        print "Not copying header",c.key
                except:
                    hdu.header.update(c.key, c.value, c.comment)
                    pass
            except:
                # This is to handle some headers added by the pipeline that are
                # not compatible with the FITS format. As example, 
                # WN_MSECSHUTCLOSED is too long of a key name.
                if (verbose):
                    print "Problem with header",c.key
                pass

        ## Insert the DETSEC header so IRAF understands where to put the extensions
	#start_x = ota_c_x * 4100
	#start_y = ota_c_y * 4100        
	#end_x = start_x + det_x2 - det_x1
	#end_y = start_y + det_y2 - det_y1
	#detsec_str = "[%d:%d,%d:%d]" % (start_x, end_x, start_y, end_y)
	#hdu.header.update("DETSEC", detsec_str, "position of OTA in focal plane")

        # Insert the new image data. This also makes sure that the headers
        # NAXIS, NAXIS1, NAXIS2 are set correctly
        data_4K = numpy.ones(shape=(4096,4096), dtype=numpy.float32)
        data_4K[:,:] = numpy.NaN
        size_x = numpy.min([data_4K.shape[0], hdulist[0].data.shape[0]])
        size_y = numpy.min([data_4K.shape[1], hdulist[0].data.shape[1]])
        data_4K[0:size_x, 0:size_y] = hdulist[0].data[0:size_x, 0:size_y]

        hdu.data = data_4K
        #hdu.verify('fix')

        # Insert into the list to be used later
        ota_list.append(hdu)

    stdout_write("\n   writing output file %s ..." % (outputfile))

    hdulist = pyfits.HDUList(ota_list)
    if (os.path.isfile(outputfile)):
	os.remove(outputfile)
    hdulist.writeto(outputfile, clobber=True)

    stdout_write(" done!\n")
    stdout_write("### complete!\n")
